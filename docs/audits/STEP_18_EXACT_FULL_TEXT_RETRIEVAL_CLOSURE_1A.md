# Step 18 — Exact and Full-Text Retrieval Baseline 1A

## Verdict

`COMPLETE AND PUSHED` once this closure record is reachable on `origin/main`.
Step 19: NOT STARTED. Step 20: NOT STARTED.

## Starting identity

- Repository baseline: `e1895e533c5f97bd06ffa2348cbdc1ee6419e42f`
- Branch: `main`
- Baseline tests: 1,195 passed
- Baseline contract validation: PASS
- Baseline compileall: PASS

The final closure identity is the commit containing this record. No unverified
future commit SHA is embedded here.

## Implemented boundary

Step 18 adds the domain-neutral `aioa_memory_kernel.retrieval` package:

- immutable, canonical, hash-bound request, candidate, and result contracts;
- exact source/version/chunk/official/document/version identity lookup;
- structured statute plus section lookup over existing metadata;
- German full-text and keyword retrieval through the pinned CockroachDB
  lexical capability;
- a parameterized read-only repository and short transaction service;
- stable fail-closed reason codes, deterministic ordering, and strict resource
  limits.

The request verifies and binds the complete Step 17 route, selected HAT
identity and manifest digest, effective scope, tenant, user, request, and HAT
scope. `PASS_THROUGH` performs no query and `AMBIGUOUS` is denied.

## Database and hard-filter decision

No migration is added. Step 18 reuses the Step 4 knowledge lineage,
`chunk_search_documents`, German `TSVECTOR`, and
`chunk_search_documents_vector_idx`, plus Step 9 Source Registry publication,
authority, provenance, and access state.

The `trusted_scope` query boundary applies tenant, HAT, manifest, route scope,
`PUBLISHED` state, authority, access, owner, and lineage predicates before
exact or lexical candidate generation. Every lineage join preserves composite
tenant and HAT identity. Source authority is a hard filter rather than a rank
boost. Shared and private paths remain distinct.

## Validation

The focused suite covers route hash and identity tampering, PASS/AMBIGUOUS
behavior, all closed exact selectors, structured statute/section lookup,
German FTS and keyword bounds, parameterization, SQL injection as data,
publication/authority/access negatives, tenant/HAT/user isolation, candidate
and result hashes, immutable output, and absence of Step 19/20 APIs.

The controlled validator uses one verified real Step 16 German Law publication
item and two bounded provision records. It applies only existing migrations to
an owned disposable CockroachDB v26.2.4 database, proves migration replay,
exercises all four modes, records sanitized index/`EXPLAIN` evidence, and
proves cross-tenant, cross-HAT, unpublished, and weak-authority exclusion.
Cleanup sends a graceful termination request to the exact owned PID, uses no
force kill, verifies that the PID exited and both owned ports closed, and
removes the exact temporary runtime. The committed run records that the CLI
drain path was unavailable, rather than overstating drain completion.

Sanitized evidence is committed at
`docs/evidence/retrieval/step18-exact-fulltext-retrieval-validation.json`.

Final validation requires:

- Step 18 focused tests: PASS;
- complete repository discovery: PASS;
- Step 10, Step 16, Step 17, HAT, German Law, authority, tenant,
  CockroachDB schema/persistence, and serialization regressions: PASS;
- contract validator and compileall: PASS;
- controlled disposable CockroachDB validation: PASS;
- static effect and Step 19/20 leakage checks: PASS.

## Authority and isolation invariants

- Model or provider authority added: NO.
- HAT execution authority added: NO.
- Route bypass added: NO.
- Post-search tenant/HAT filtering substituted for hard SQL filters: NO.
- Cross-tenant, cross-HAT, or cross-user-private retrieval added: NO.
- Caller-controlled SQL structure added: NO.
- Database or source-bundle mutation added by production retrieval: NO.
- AWS, S3, provider, model, or network operation added: NO.

## Known limitations and handoff

Step 18 returns bounded lexical candidates, not answers or a final Evidence
Bundle. It does not select question-time legal applicability among versions.
It adds no embeddings, vector generation/search, semantic retrieval, hybrid
fusion, reranking, diversity, or context-budget logic.

Step 19 may reuse the route-bound request, immutable candidate/result
contracts, hard `trusted_scope` eligibility, and Source Registry boundary when
adding a separately reviewed embedding/vector foundation. Step 20 remains the
owner of hybrid fusion and final deterministic ranking.

Step 19 and Step 20 remain NOT STARTED in this closure.
