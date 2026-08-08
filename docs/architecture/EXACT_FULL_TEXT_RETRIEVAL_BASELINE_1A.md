# Exact and full-text retrieval baseline 1A

## Purpose

Step 18 introduces one read-only, route-bound retrieval boundary. It returns
bounded immutable candidates from the existing CockroachDB knowledge lineage
and Source Registry. It does not answer a question, assemble an Evidence
Bundle, invoke a model, or authorize an action.

The domain-neutral package is `aioa_memory_kernel.retrieval`. German Law is
the first controlled fixture, while legal statute/section interpretation is
limited to structured metadata already published by Steps 13 to 16.

## Mandatory Step 17 binding

Every `RetrievalRequest` contains the exact `KnowledgeRouteResult` and repeats
the security-critical identity for explicit comparison:

- request, tenant, and user identity;
- route hash;
- selected HAT ID and version;
- selected typed manifest digest;
- effective scope;
- exact HAT scope ID.

The request constructor verifies the Step 17 route hash and rejects every
mismatch. `HAT_ASSIST` and `HAT_ENFORCE` may enter retrieval. `PASS_THROUGH`
returns a deterministic `NO_HAT_SELECTED` result without opening a database
transaction. `AMBIGUOUS` fails closed. The selected manifest digest is also a
hard predicate against the existing HAT manifest row before candidates exist.

## Closed retrieval modes

| Mode | Semantics |
| --- | --- |
| `EXACT_IDENTIFIER` | Canonical equality over a closed selector: source, knowledge version, chunk, official identifier, document identity, or version identity. |
| `STATUTE_SECTION` | Equality over the existing structured `official_identifier` plus `provision_identifier`; body text cannot substitute either field. |
| `FULL_TEXT` | German lexical retrieval through `plainto_tsquery('german', value)`, `@@`, and retrieval-local `ts_rank`. |
| `KEYWORD` | Sorted, unique, bounded keywords joined into the same German lexical query; no model expansion or synonym authority. |

Caller values are parameters. Callers cannot supply a table, column,
operator, JSON path, `ORDER BY`, SQL fragment, or raw tsquery expression.
Exact matching has no substring, case-folding, fuzzy, or model fallback.

## Hard filtering before candidate generation

Every query begins from a `trusted_scope` CTE. Before exact matching or FTS it
binds:

- tenant ID and HAT scope ID;
- selected HAT ID, version, and manifest digest;
- Step 17 jurisdiction, language, source-class, and supported scope values;
- Source Registry state `PUBLISHED`;
- approved authority levels `OFFICIAL_PRIMARY` or
  `AUTHORITATIVE_SECONDARY`;
- acceptable redaction state and `model_generated=false`;
- target scope, access class, exact owner user, and personal-memory-space
  identity where applicable.

All lineage joins retain both `tenant_id` and `hat_scope_id`. Shared retrieval
admits only `PUBLIC` and `TENANT_RESTRICTED` rows with no owner fields. Private
retrieval selects `USER_PERSONAL_HAT` and requires exact tenant, user, and
personal-memory-space identity. These are SQL admission predicates, not a
post-search Python filter. The service rechecks candidate identity and scope
as defense in depth.

Unknown required scope dimensions fail closed. `knowledge_as_of` may be
carried unchanged, but Step 18 deliberately returns separately identified
matching versions and never chooses which law applied at question time. That
decision remains Step 21.

## Existing database reuse

No migration is added. The repository reuses:

- `knowledge_sources`;
- `source_snapshots`;
- `knowledge_versions`;
- `knowledge_chunks`;
- `chunk_search_documents`;
- `source_registry_entries`;
- `hat_scopes` and `hat_manifests`;
- the existing German `TSVECTOR` and
  `chunk_search_documents_vector_idx` inverted index.

The production repository issues only parameterized `SELECT` statements in a
short existing persistence transaction. It contains no insert, update,
delete, upsert, or DDL path. Disposable validation setup is a separate script
and cannot be reached through the production repository.

## Candidate and result integrity

Requests, candidates, and results are frozen/slotted and use the existing
canonical JSON/SHA-256 implementation. A candidate preserves source authority,
publication state, registry/scope/artifact digests, snapshot and version
identity, structured metadata, content SHA-256, and the exact route scope.
The content digest is checked against exact UTF-8 bytes.

Full-text rank is converted to a bounded canonical decimal string before it
enters a hash. Exact ordering uses version ordinal, chunk ordinal, and chunk
ID. Lexical ordering uses local `ts_rank` descending followed by the same
stable identities. This is retrieval-local ordering only, not Step 20 final
ranking.

The default result limit is 20 and the hard maximum is 100. Query text is at
most 4,096 UTF-8 bytes, exact IDs at most 16, keywords at most 32, one returned
candidate at most 64 KiB, and total returned content at most 1 MiB. One extra
row is fetched only to prove truncation.

## Source authority and temporal limits

Source authority is a hard eligibility boundary, never a ranking boost.
Unpublished, quarantined, withdrawn, rejected, weak-authority, mismatched
tenant, mismatched HAT, or wrong-owner records cannot become candidates. The
German Law `AUTHORITATIVE_SECONDARY` assessment remains unchanged; Step 18
does not upgrade it to an official primary source.

Existing version, current-state, document, provision, and temporal metadata
are carried when present. Multiple valid versions remain separate. Step 18
does not decide historical applicability, freshness, or conflict resolution.

## Non-goals and next boundaries

Step 18 adds no embedding generation, vector column or search, semantic
similarity, model query expansion, hybrid fusion, reranking, diversity logic,
context-budget optimization, final Evidence Bundle, answer generation, or
provider call. Step 19 owns embedding/vector foundations. Step 20 owns hybrid
candidate fusion, deterministic final ranking, and Evidence Bundle assembly.
