# Embedding Generation and Vector Retrieval Foundation 1A

## Boundary

Step 19 adds one local embedding generation and vector retrieval foundation.
It consumes the verified Step 17 `KnowledgeRouteResult` and the Step 18 trusted
scope SQL boundary. It returns immutable vector candidates; it does not merge
them with lexical candidates and does not create a final Evidence Bundle.

The embedding model is data transformation machinery, never authority. Vector
distance cannot select a HAT, widen a route, change publication state, upgrade
source authority, or bypass tenant, HAT, access, owner, redaction, and lineage
filters.

## Pinned model identity

Step 19 V1 admits exactly:

- model: `intfloat/multilingual-e5-small`;
- revision: `fd1525a9fd15316a2d503bf26ab031a61d056e98`;
- weights: `model.safetensors` with SHA-256
  `1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477`;
- dimension: 384;
- maximum tokenizer length: 512;
- input policy: `e5-query-passage-prefix-v1`;
- query prefix: `query: `;
- passage prefix: `passage: `;
- normalization: unit L2;
- license: MIT.

The checked-in config is strict and digest-bound. Model ID, revision, backend,
dimension, weights, prefixes, normalization, and token policy all contribute
to `model_digest`. Floating revisions, pickle weights, remote code, caller
model selection, and remote inference are rejected.

The heavy Transformers/Torch runtime is imported lazily by the local E5
backend. Ordinary imports and unit tests need no model runtime or network. The
controlled validator installs exact dependencies in an ignored environment,
downloads only the immutable revision when absent, verifies every required
model file and the safetensors hash, then reloads with local-files-only and
offline flags.

## Embedding identity

Passage input binds tenant, HAT scope, source, knowledge version, chunk,
content SHA-256, model digest, input-policy version, and prepared passage
SHA-256. Query input additionally binds the Step 17 route, user, selected HAT,
effective scope, and query digest. Raw query vectors are ephemeral and are not
persistently cached.

Vectors contain exactly 384 finite non-zero values. The trusted backend
normalizes them before use. Canonical vector identity is the SHA-256 of 1,536
bytes: 384 IEEE-754 float32 values in little-endian order. Native floats never
enter canonical JSON hash material. Retrieval distance is represented as a
bounded canonical decimal string.

The E5 tokenizer owns deterministic truncation to 512 tokens and returns an
explicit truncation receipt. The original chunk identity remains unchanged.

## External derived cache

Passage vectors are derived, non-authoritative, rebuildable artifacts below the
verified Step 8 external-volume `embeddings` root. Cache keys bind the model
and semantic passage identity. Fan-out paths are content-addressed relative
paths. Writes use the existing exact atomic-write boundary.

A cache replay requires a regular non-symlink file, exact path, exact 1,536
byte length, and exact SHA-256. Corruption is rejected; it cannot override
source identity or authority. There is no system-drive fallback. Model files,
runtime environments, cache vectors, and machine-local manifests are not
committed.

## Generation and persistence

Generation is deterministic and bounded: default batch size 32, maximum batch
size 64, default run maximum 1,000, and hard run maximum 10,000. Candidate
chunks come only from the Step 18 trusted scope, in stable lineage order.

The transaction shape is short read, close transaction, local inference/cache,
then short persistence transaction. A transaction is never held across model
inference. Exact chunk/model replay is verified and reused. A conflicting
replay fails closed; old model generations are never rewritten.

Migration `0010_step19_embedding_vector_retrieval` adds one table,
`memory_patch.chunk_embeddings`. Its composite foreign key preserves tenant,
chunk, knowledge-version, source, and HAT-scope lineage. Its primary identity
allows distinct immutable model generations. SQL checks pin model identity,
dimension 384, and digest shapes.

The table has RLS and FORCE RLS, is owned by `mp_schema_owner`, and grants only
SELECT/INSERT to `mp_app_runtime`. Its policies reuse the Step 5 request
context. An ordinary model-generation lookup index binds tenant, HAT, model,
and chunk. The L2 vector index uses the already proven empty-table form:

```sql
CREATE VECTOR INDEX chunk_embeddings_vector_l2_idx
  ON memory_patch.chunk_embeddings (
    tenant_id,
    hat_scope_id,
    embedding vector_l2_ops
  );
```

The pinned CockroachDB cluster capability
`feature.vector_index.enabled=true` is an explicit deployment precondition.
An index prefix is a planning aid, not authorization.

## Vector retrieval

Public retrieval accepts query text, never a caller vector or SQL fragment.
The service verifies the complete Step 17 route and binds request, tenant,
user, route hash, selected HAT ID/version/manifest digest, effective scope,
HAT scope, model digest, query digest, and result limit. `HAT_ASSIST` and
`HAT_ENFORCE` are eligible. `PASS_THROUGH` returns a deterministic no-HAT
result; `AMBIGUOUS` fails closed.

The shared Step 18 trusted-scope CTE applies tenant, HAT, manifest, effective
route scope, publication, authority, access/owner, redaction,
model-generated-source, and composite lineage predicates before vector
candidate admission. The embedding join additionally binds the approved model
digest and exact chunk lineage. Search uses normalized vectors with L2 `<->`,
then stable knowledge-version ordinal, chunk ordinal, and chunk ID tie-breaks.
Default result count is 20 and the hard maximum is 100; query text is limited
to 4,096 UTF-8 bytes.

## Authority and isolation

- The model cannot select a HAT, tenant, source, scope, or publication state.
- Similarity is retrieval-local candidate evidence, not authority.
- Tenant and HAT identity are hard SQL predicates before candidates.
- Source Registry `PUBLISHED` state and approved authority classes remain hard
  predicates.
- PUBLIC, TENANT_RESTRICTED, and USER_PRIVATE owner boundaries remain intact.
- RLS/FORCE RLS provides defense in depth using an ordinary runtime role.
- No provider, AWS, S3, HTTP inference, approval, or execution capability is
  added.

## Step 20 boundary and limitations

Step 19 does not implement lexical/vector fusion, Reciprocal Rank Fusion,
reranking, diversity, context-budget allocation, or final Evidence Bundle
assembly. Step 20 owns those decisions and must preserve this hard scope and
source-authority boundary before combining candidates.

Step 19 is a bounded capability foundation, not an embedding-quality
benchmark. The controlled German fixture proves local semantic ordering for a
small known case only. Question-time temporal selection remains Step 21.
