# Step 19 - Embedding Generation and Vector Retrieval Foundation 1A

## Verdict

`COMPLETE AND PUSHED` once this closure record is reachable on `origin/main`.
Step 20: NOT STARTED. Step 21: NOT STARTED.

## Starting identity

- Repository baseline: `71b85b42a25921f38e3d36b44f51a3be7ff1c710`
- Branch: `main`
- Baseline tests: 1,278 passed
- Baseline contract validation: PASS
- Baseline compileall: PASS

The final closure identity is the commit containing this record. No unverified
future commit SHA is embedded here.

## Implemented boundary

Step 19 adds the domain-neutral `aioa_memory_kernel.embeddings` package with:

- a strict checked-in model identity and deterministic model digest;
- immutable, canonical, hash-bound embedding generation, record, vector
  request, candidate, and result contracts;
- a lazy local E5 backend with exact safetensors verification and offline
  reload;
- canonical 384-value little-endian float32 vector identity;
- a content-addressed, hash-verified external passage cache;
- bounded generation with model inference outside transactions;
- idempotent lineage- and model-bound persistence;
- parameterized, route-bound vector retrieval using the Step 18 trusted scope.

No public API accepts a model ID, revision, callable, raw vector, SQL fragment,
remote endpoint, provider credential, or execution authority.

## Model and cache identity

The only approved model is `intfloat/multilingual-e5-small` at revision
`fd1525a9fd15316a2d503bf26ab031a61d056e98`, dimension 384, maximum 512
tokens, MIT license, and `model.safetensors` SHA-256
`1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477`.
The exact E5 `query: ` / `passage: ` policy and unit-L2 normalization are part
of the model digest.

The model runtime, model files, passage vectors, and local installation
manifest live only below the verified external volume. Cache artifacts are
derived and rebuildable. Exact length/hash and regular-file checks are required
on replay; no system-drive fallback exists. Raw user queries and query vectors
are not persistently cached.

## Database closure

Migration `0010_step19_embedding_vector_retrieval` adds only
`memory_patch.chunk_embeddings`. It binds the exact Step 4/11/18 composite
chunk lineage, permits immutable model generations, pins dimension 384, and
adds:

- an empty-table `tenant_id, hat_scope_id, embedding vector_l2_ops` vector
  index;
- an ordinary tenant/HAT/model/chunk lookup index;
- least-privilege SELECT/INSERT grants;
- RLS and FORCE RLS policies using the Step 5 request context.

The migration manifest and runner are extended to schema version 8 without
changing earlier migration hashes. Replay is a no-op. The controlled node
explicitly verifies/enables the pinned vector-index capability before applying
the migration.

## Authority, scope, and transaction invariants

Vector generation and retrieval reuse the shared Step 18 trusted-scope SQL.
Tenant, HAT, route manifest/effective scope, `PUBLISHED` state, source
authority, access/owner, redaction, model-generated-source, and composite
lineage predicates apply before vector candidate admission. Model digest is an
additional hard predicate.

An index prefix is not authorization. Similarity cannot upgrade source
authority. The model cannot change routing, tenant, HAT, scope, publication,
or owner identity. An ordinary runtime login proves cross-tenant RLS denial.

Generation uses a short trusted-source read, closes that transaction, performs
local inference/cache work, then uses a short insert/replay transaction. Only
approved serialization failures are retried by the existing persistence
boundary.

## Validation

The focused suite covers exact model pinning, digest changes, no dynamic model
selection, vector dimension/finite/normalization/byte identity, cache
replay/corruption/symlink negatives, generation bounds and transaction order,
route tampering, shared hard SQL filters, parameterization, idempotency,
candidate/result hashes, migration/FK/index/RLS contracts, Step 18 regression,
model non-authority, and the Step 20 boundary.

The controlled validator verifies the real pinned model and six required model
files, reloads offline, proves a bounded German semantic fixture, reads one
verified Step 16 publication item, generates two passage embeddings, exercises
cache replay, applies/replays all migrations on one owned disposable
CockroachDB v26.2.4 node, persists/replays embedding records, and proves vector
ordering plus cross-tenant, cross-HAT, unpublished, weak-authority, and
wrong-model exclusion. Index inventory and sanitized `EXPLAIN` evidence are
recorded separately from authorization claims.

Sanitized evidence is committed at
`docs/evidence/retrieval/step19-embedding-vector-validation.json`.

Final validation requires:

- Step 19 focused and complete repository tests: PASS;
- Step 10, Step 16, Step 17, Step 18, HAT, German Law, authority, tenant,
  external-volume, CockroachDB schema/persistence, and serialization
  regressions: PASS;
- contract validator and compileall: PASS;
- real-model, external-cache, and disposable CockroachDB validation: PASS;
- cleanup and static remote-inference/Step 20 leakage checks: PASS.

## Resource bounds

- default/maximum generation batch: 32/64;
- default/hard maximum items per run: 1,000/10,000;
- default/maximum vector results: 20/100;
- maximum query UTF-8 bytes: 4,096;
- vector bytes per cache artifact: 1,536;
- persistent query-vector cache: disabled.

## Known limitations and handoff

The real German fixture is a bounded capability proof, not a quality benchmark
or legal-answer assertion. Vector retrieval returns candidate evidence only.
It performs no question-time temporal selection.

Step 20 may combine the immutable Step 18 lexical candidates and Step 19
vector candidates only after preserving their shared hard scope and source
authority boundary. Hybrid fusion, reranking, diversity, context budgets, and
final Evidence Bundle assembly remain NOT STARTED here.
