# ADR-026: Embedding generation and vector retrieval foundation

## Status

Proposed. It becomes accepted only when the Step 19 closure commit is
reachable on `origin/main`.

## Context

Step 18 established exact and German lexical candidates behind a verified
Step 17 route and one hard trusted-scope boundary. The repository's pinned
CockroachDB v26.2.4 capability evidence proves `VECTOR`, L2 distance, and an
empty-table prefix vector index. Step 8 provides the fail-closed external
derived-storage boundary.

Step 19 needs reproducible embeddings and vector-local candidates without
turning a model, cache, similarity score, or index prefix into authority.

## Decision

1. Step 19 V1 uses only `intfloat/multilingual-e5-small` at immutable revision
   `fd1525a9fd15316a2d503bf26ab031a61d056e98`, with verified safetensors
   SHA-256, dimension 384, and maximum 512 tokens.
2. Inference is local-only, uses no remote code or pickle weights, and applies
   the exact E5 `query: ` / `passage: ` asymmetric prefix policy.
3. All query and passage vectors are unit L2 normalized. Canonical vector
   identity is SHA-256 over 384 little-endian float32 values; native floats do
   not enter JSON security hashes.
4. Passage cache is content-addressed, model-versioned, external-volume-only,
   derived, rebuildable, and hash-verified. Raw user-query vectors are not
   persistently cached.
5. Migration 0010 adds one lineage-bound `VECTOR(384)` embedding table. Model
   generations coexist immutably and exact replay is idempotent.
6. The vector index uses `vector_l2_ops` with tenant/HAT prefixes and is created
   while the table is empty. The pinned vector feature setting is an explicit
   cluster precondition. Index prefixes are not authorization.
7. RLS/FORCE RLS and least-privilege grants apply to the new table.
8. Generation and vector retrieval reuse the Step 18 trusted-scope SQL before
   source selection or candidate admission. Source authority and publication
   state are hard eligibility rules, not similarity boosts.
9. Model inference occurs outside database transactions. Reads and writes are
   separate short, retry-bounded transactions.
10. Step 19 returns vector candidates only. Step 20 owns hybrid fusion and
    final deterministic ranking.

## Consequences

Changing model revision, weights, prefixes, normalization, dimension, or
backend contract creates a different model digest and generation. Cached or
persisted vectors cannot silently cross generations.

Operators must prepare a verified external volume, exact local runtime/model
cache, and the pinned CockroachDB vector capability before generation. The
normal runtime then operates offline.

The vector repository retains the same tenant, HAT, route, source-authority,
publication, access, owner, redaction, and lineage boundary as lexical
retrieval. Similarity can order only the already eligible vector candidates.

## Rejected alternatives

### Floating model revisions or caller-selected models

Rejected because results and persisted generations would not have one stable
identity.

### Hosted embedding inference

Rejected because it adds provider credentials, network dependence, and an
unapproved authority/data-egress boundary.

### Cache on the repository or system drive

Rejected because large derived artifacts belong to the verified external
volume and no fallback is allowed.

### Global ANN followed by Python filtering

Rejected because forbidden tenant/HAT/source candidates would already have
entered generation and timing behavior.

### Hybrid retrieval in Step 19

Rejected because Step 20 separately owns fusion, ranking, diversity, context
budgets, and final Evidence Bundle assembly.
