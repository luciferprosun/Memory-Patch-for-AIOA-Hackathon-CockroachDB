# Temporal Resolver, Conflict Detection and Freshness Policy 1A

## Boundary

Step 21 consumes the canonical Step 20 `HybridEvidenceOutcome` and frozen
Evidence Bundle. It does not create another evidence-bundle type, retrieve
new data by itself, invoke a model, or produce an answer. The public service
accepts only typed Step 17 and Step 20 contracts and produces an immutable
`TemporalResolutionResult`.

The request and result preserve request, tenant, user, route hash, selected
HAT/version/manifest, effective scope, Step 20 outcome/bundle hashes, and the
independent Step 17 answer and execution decisions. Step 21 can restrict
evidence; it cannot widen any upstream boundary or grant authority.

## Question-time contract

`TemporalQueryMode` is closed:

- `CURRENT` evaluates at an injected trusted UTC clock;
- `AS_OF` requires an explicit timestamp at or before trusted current time;
- `FUTURE` requires an explicit timestamp at or after trusted current time;
- `UNSPECIFIED` records that no explicit as-of value was supplied and uses the
  same trusted clock without pretending the caller supplied currentness.

Every timestamp is timezone-aware, normalized to UTC, and hash-bound. Model,
provider, source metadata, process start time, and network time cannot provide
the trusted clock. If `knowledge_as_of` is already present in the Step 17
effective scope, the request must match it exactly.

## Temporal facts and applicability

The domain-neutral extractor consumes explicit structured metadata already
preserved in the Step 20 item. It recognizes legal effect/application dates,
decision date, explicit version status and supersession facts, and separate
operational observation timestamps. It never infers a date from filename,
insertion order, ranking, or prose.

Intervals use one immutable V1 rule:

```text
start <= as_of and (end is absent or as_of < end)
```

The result distinguishes `APPLICABLE`, `NOT_YET_APPLICABLE`, `EXPIRED`,
`SUPERSEDED`, `UNKNOWN`, and `CONFLICTING`. A current flag cannot override
exact dates. Historical queries can select a predecessor before an explicit
successor becomes applicable; future-effective candidates remain visible but
are not selected early.

When metadata uses the Step 21 canonical temporal-facts scheme, its digest is
recomputed and verified. Older Step 15 aggregate digests are preserved
without a false recomputation claim because their ordered normalization-record
preimage is not carried in the Step 20 projection. That limitation is explicit
in the result.

## Supersession and conflict preservation

Supersession uses only explicit `supersedes`, `superseded_by`, and
`superseded_at` facts. An applicable successor marks its applicable
predecessor superseded for that as-of time. Missing successors yield unknown;
branches and graph cycles yield conflict.

Material conflict detection groups candidates only when they refer to the
same exact logical document/provision/scope and carry incompatible applicable
content or temporal integrity. Independent sources with identical canonical
content remain supporting evidence. Each conflict group is deterministically
derived from its subject, ordered Step 20 item hashes, and reason codes. The
resolver never synthesizes merged legal text or chooses a winner by rank.

## Freshness is independent

`FreshnessStatus` is `FRESH`, `STALE`, `UNKNOWN`, or `NOT_APPLICABLE`.
Freshness is evaluated only for otherwise applicable evidence. It uses an
explicit, versioned, digest-bound `FreshnessPolicy` containing reviewed
source-kind thresholds and a fixed observation-field precedence. No universal
threshold exists in the core.

A missing source-kind policy or required observation produces `UNKNOWN`.
Operational observation time influences freshness only; it cannot replace
legal effective/application time. A candidate can therefore be applicable
and stale, or recently observed and legally inapplicable.

## Completeness fallback

Policy `temporal-completeness-fallback-1a`, version `1`, permits at most one
attempt. Fallback may inspect one additional already-produced, verified Step
20 outcome only when its route, tenant/user, HAT, manifest, effective scope,
Step 17 policy, retrieval policy, and embedding-model bindings equal the
primary bundle exactly.

Duplicate item hashes are not reconsidered. There is no recursive loop,
network lookup, provider activation, model selection, or liberal fallback to
unpublished or out-of-scope evidence. Failure remains explicit
`INSUFFICIENT`.

## Result and evidence status

Each `TemporalCandidateAssessment` binds the Step 20 item and bundle hash,
lineage, temporal facts/digest, evaluation timestamp, applicability,
freshness, supersession, conflict identity, selection flag, reasons, and its
own hash. `TemporalResolutionResult` binds all assessments, selected item
hashes, excluded assessment hashes, conflict groups, freshness counts,
fallback summary, policies, statuses, limitations, and final result hash.

Status priority is fail closed:

1. known availability failure -> `UNAVAILABLE`;
2. integrity failure -> `INVALID`;
3. unresolved material conflict -> `CONFLICTING`;
4. missing applicable coverage, partial Step 20 coverage, or unknown required
   freshness -> `INSUFFICIENT`;
5. applicable but expired freshness policy -> `STALE`;
6. bounded complete applicable/fresh input -> `SUFFICIENT`.

`SUFFICIENT` is relative only to the verified bounded Step 20 input and the
explicit completeness policy. Evidence status remains separate from answer
status. `HAT_ENFORCE` still grants no execution or approval authority.

## Persistence and effects

No migration is added. Step 21 is pure resolver/policy logic and persists
nothing. It performs no database query, network call, provider/model call,
filesystem mutation, AWS/S3 action, approval, commit, or execution.

## Validation boundary

The controlled validator verifies the real committed Step 16 evidence digest,
the real Step 20 evidence digest/bundle identity, and then exercises the
actual typed Step 20-to-Step 21 integration. The locally available published
fixture does not contain a verified multi-version temporal family. Historical,
future, supersession, conflict, stale, unavailable, and fallback edge
semantics therefore use an explicitly synthetic deterministic family. No real
historical conflict is claimed.

## Step 22 handoff and non-goals

Step 22: NOT STARTED.

Step 22 may consume the verified `TemporalResolutionResult`, selected Step 20
item hashes, conflict groups, freshness summary, limitations, final evidence
status, and unchanged Step 17 policy boundary. It must produce an uncorrected
Draft V1 without letting the provider/model rewrite route, source authority,
temporal decisions, conflict state, or evidence status.
