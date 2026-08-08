# Step 20 - Hybrid Retrieval, Evidence Bundle and Deterministic Ranking 1A

## Verdict

`COMPLETE AND PUSHED` once this closure record is reachable on `origin/main`.
Step 21: NOT STARTED. Step 22: NOT STARTED.

## Starting identity

- Exact Step 19 baseline: `c8d0258bf38b207b29a1a4d1121172ba26f11caa`
- Baseline subject: `feat(retrieval): add embedding and vector foundation 1a`
- Branch: `main`
- Baseline tests: 1,351 passed
- Baseline contract validation and compileall: PASS

The final closure identity is the commit containing this record. No unverified
future commit SHA is embedded here.

## Implemented boundary

Step 20 adds the domain-neutral `aioa_memory_kernel.evidence` package. It
consumes typed Step 18 lexical and Step 19 vector pairs, revalidates their
hashes and shared Step 17 route/policy identity, and rechecks hard candidate
eligibility before deterministic fusion.

Canonical tenant/HAT/source/version/chunk/content identity deduplicates a
candidate across modalities. Every modality contribution remains separately
hash-bound. Conflicting security metadata fails closed.

## Ranking, diversity, and context

The immutable V1 ranking policy uses integer RRF (`K=60`, scale
`1,000,000,000`) and fixed weights 8/8/4/3/2 for statute-section, exact,
full-text, vector, and keyword. Exact structured results outrank vector-only
similarity. Raw scores are retained only as canonical strings and never enter
the fused integer calculation.

Deterministic exact-first diversity enforces 40 items globally, 3 per source,
4 per version, and 8 exact-priority items. Context assembly uses a 65,536-byte
default, 262,144-byte maximum, 8,192 bytes per excerpt, UTF-8-safe prefixes,
and a 524,288-byte canonical bundle cap.

## Evidence Bundle and persistence

The frozen bundle binds the Step 20 request hash, requested modalities,
caller-reduced item limit, route and policy results, all upstream hashes, HAT
and model identity, ordered item membership, policies, counts, exclusions,
coverage, statuses, and exact excerpt bytes. Evidence status remains distinct
from answer status and execution authorization.

No migration is added. Existing Step 4 bundle persistence requires an exact
existing `kernel_run_id` not present in Step 17-19 retrieval contracts. Step 20
does not impersonate it with `request_id` or hide the gap in JSON metadata.
Persistence is therefore explicitly not applicable at this boundary; in-memory
bundle replay is deterministic.

## Validation

- Step 20 focused suite: 64/64 PASS.
- Full repository suite: 1,415/1,415 PASS.
- Required Step 10/16/17/18/19, HAT, German Law, authority, tenant,
  external-volume, CockroachDB, and serialization regressions: 681/681 PASS.
- Contract validator: PASS.
- Compileall: PASS.
- Controlled real Step 18/19 validation: PASS.

Controlled validation used four actual Step 18 result families and one actual
local Step 19 vector result for the same route. Seven modality candidates
deduplicated to two exact lineage identities. The final two-item bundle hash
was `96338f1ce66875a7698f0135f51a90da06e8d8e6c6b1425f2e39ab0c89cfae7c`.
Input permutation replayed identically; metadata conflict and wrong-model
inputs failed closed.

The owned CockroachDB PID exited, loopback ports closed, and temporary store
was removed without force kill. Step 19 caches and Step 16 source bytes were
preserved. Sanitized evidence is committed at
`docs/evidence/retrieval/step20-hybrid-evidence-bundle-validation.json`.

## Authority and isolation

Source authority and publication remain hard admission facts. Vector
similarity and modality count grant no authority. Tenant, HAT scope, route
scope, access class, owner/personal space, and model digest are revalidated.
The bundle creates neither answer nor execution authority and calls no model,
provider, AWS, or S3 service.

## Known limitations and Step 21 handoff

`COMPLETE` is bounded retrieval coverage, not global corpus completeness or a
legal conclusion. Step 20 preserves temporal/version metadata but does not
decide historical applicability, future effect, repeal, supersession,
freshness, staleness, or legal conflict.

Step 21 may consume the frozen ordered items, structured temporal facts,
source authority, and retrieval coverage without rewriting candidate identity
or Step 20 ordering. Step 21 remains NOT STARTED in this closure.
