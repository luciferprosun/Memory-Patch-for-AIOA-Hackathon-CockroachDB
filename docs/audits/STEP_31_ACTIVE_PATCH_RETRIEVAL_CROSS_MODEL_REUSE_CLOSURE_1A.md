# Step 31 Active Patch Retrieval and Cross-Model Reuse Closure 1A

## Starting point and scope

- Exact Step 30 base: `753e14b0a079bd48466f694435587a2b5acbe4ca`.
- Step 30 was complete and pushed; Step 31 and Step 32 were not started.
- Scope is limited to read-only ACTIVE Personal Memory retrieval, query-time
  applicability and provider/model-neutral reuse.
- Step 32 remains not started. No final Step 31 Git commit SHA is asserted in
  this record before the closure commit exists.

## Retrieval architecture

Step 31 adds immutable, hash-bound retrieval policy/request, per-patch
assessment, retrieved-patch result and private context-envelope contracts.
The request binds the exact authenticated owner, tenant, slot, route/scope,
Step 21 temporal result, Step 22 model identity, query digest and bounded
result count. Raw query text is not persisted in the contract.

The repository performs one parameterized, owner-scoped, ACTIVE-only query
and reconstructs the exact Step 29/30 lifecycle. Activation, commit, approval,
validation, proposal and candidate receipts/hashes, persisted content/scope
and state versions must all agree before a patch can be assessed.

## Applicability and cross-model reuse

Eligibility requires current Step 27 slot state, exact route scope, trusted
temporal applicability, current enabled model binding and compatible current
canonical evidence. Conflict or detached current evidence suppresses the
patch without persisting a revocation.

The patch is model-independent. Controlled validation proves two distinct
provider-neutral exact model identities retrieve one identical patch ID/hash
and statement SHA-256 while a third unbound identity is denied. No Gemma,
model call or provider credential is required.

## Persistence and isolation decision

No migration is added. Existing Step 27-30 carriers, RLS/FORCE RLS policies
and `memory_items_scope_retrieval_idx` support the bounded read path. Hard SQL
filters include exact tenant, owner, slot, HAT scope and ACTIVE state before
Python. Same-tenant cross-user and cross-tenant real database sessions see no
private patch.

Retrieval defaults to 8 results, permits at most 32, considers at most 128
candidates and reports deterministic truncation identities. State before and
after controlled retrieval is identical.

## Canonical evidence and authority

The context envelope is explicitly `PRIVATE USER MEMORY - NON-CANONICAL`.
An active patch is not source-registry material, an Evidence Bundle item,
citation authority, answer authority, approval authority or execution
authority. Current canonical evidence cannot be overridden by the patch.

Step 31 exposes no approval, commit, activation, update, delete, supersession,
revocation, export or promotion method. Step 32 functionality is absent.

## Validation evidence

The controlled disposable CockroachDB validation passed:

- Step 31 focused module: 26 tests, zero failures/errors;
- explicit Step 17-31, RLS, persistence, authority, isolation and contract
  regressions: 914 tests, zero failures/errors;
- full repository discovery: 1,750 tests, zero failures/errors;
- Python compile-all, contract validation, offline migration validation and
  `git diff --check`: pass;
- exact pinned CockroachDB `v26.2.4` and 14-migration replay;
- existing retrieval index and RLS/FORCE RLS catalog proof;
- ACTIVE retrieval and non-active exclusions;
- Model A/Model B same-patch reuse and Model C denial;
- scope, temporal and canonical-conflict negatives;
- real same-tenant cross-user and cross-tenant denial;
- zero semantic state mutations and complete cleanup;
- validation digest
  `08a2706ffb0a25de78bbc48705d2ffb0ec8752721c3a702af0c55accc87c05bc`.

The canonical sanitized result is recorded in
`docs/evidence/personal-memory/step31-active-patch-retrieval-validation.json`.

## Known limitations

Step 31 uses deterministic exact metadata/scope/binding checks and does not
add vector or model-based Personal Memory matching. It computes conflict
suppression but does not persist supersession, revocation or stale state.
Patch export/deletion integration and personal-to-shared review/promotion are
reserved for Step 32.

Step 32: NOT STARTED.
