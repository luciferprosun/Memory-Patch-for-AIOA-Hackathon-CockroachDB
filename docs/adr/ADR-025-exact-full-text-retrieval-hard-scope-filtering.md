# ADR-025: Exact/full-text retrieval and hard scope filtering

## Status

Proposed. It becomes accepted only when the Step 18 closure commit is
reachable on `origin/main`.

## Context

Step 17 established a deterministic, hash-bound route and exact selected HAT
identity. Step 4 already established knowledge lineage, German lexical search
documents, and an inverted index. Step 9 already established Source Registry
authority, access, provenance, and publication state. Adding parallel routing,
retrieval, or source-authority state would create competing trust boundaries.

Retrieval must also prevent cross-tenant, cross-HAT, and private-owner leakage
before the database produces candidates. Filtering a global ranked set in
Python would be both unsafe and wasteful.

## Decision

1. The verified Step 17 `KnowledgeRouteResult` is mandatory input. Retrieval
   binds its route hash, selected HAT ID/version/manifest digest, request,
   tenant, user, HAT scope, and effective scope.
2. `PASS_THROUGH` performs no HAT retrieval and `AMBIGUOUS` fails closed. Only
   `HAT_ASSIST` and `HAT_ENFORCE` may query a selected HAT corpus.
3. Tenant, HAT, manifest, route scope, publication, source authority, access,
   owner, and lineage predicates are applied in SQL before exact or lexical
   candidate generation.
4. Existing Step 4 and Step 9 tables and constraints are reused. Step 18 adds
   no database migration and no duplicate Source Registry.
5. Exact retrieval uses only a closed selector vocabulary and equality.
   Statute/section retrieval uses existing structured metadata.
6. German lexical retrieval uses the pinned CockroachDB capability:
   `plainto_tsquery('german', ...)`, `@@`, `ts_rank`, and the existing
   `chunk_search_documents_vector_idx` inverted index. Caller values are
   parameters and cannot control SQL structure.
7. Source authority is an eligibility rule, not a score boost. Normal results
   require `PUBLISHED` state and an approved authority class.
8. Requests, candidates, and results are immutable, bounded, deterministic,
   and hash-bound using the existing canonical serializer. Native floating
   rank values are converted to canonical decimal strings before hashing.
9. Step 18 returns retrieval candidates, not a final Evidence Bundle or an
   answer. It grants no execution or model authority.
10. Embedding generation and vector retrieval are deferred to Step 19.
    Hybrid fusion, final ranking, diversity, context budgeting, and Evidence
    Bundle assembly are deferred to Step 20.

## Consequences

Exact and German lexical candidates can be reproduced from an exact Step 17
route without broad corpus scans or post-search isolation. Existing RLS remains
defense in depth, while query structure itself preserves composite tenant and
HAT identity through every lineage join.

The hard authority policy initially admits only official primary and
authoritative secondary published sources. Changing that closed set is a
future reviewed policy change, not a caller option. Question-time temporal
selection remains unresolved and all matching versions stay visible.

## Rejected alternatives

### Search globally and filter after ranking

Rejected because forbidden candidates would already have entered generation,
ranking, memory, and timing behavior.

### Add Step 18 retrieval tables

Rejected because the Step 4 schema already provides chunks, German search
documents, and the inverted index, while Step 9 owns source authority.

### Accept caller-supplied SQL or tsquery syntax

Rejected because it would move query authority to untrusted input and break
the closed deterministic contract.

### Add vectors or hybrid ranking now

Rejected because those are separately bounded canonical Steps 19 and 20.
