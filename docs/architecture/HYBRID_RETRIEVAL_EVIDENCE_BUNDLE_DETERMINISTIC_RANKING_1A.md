# Hybrid Retrieval, Evidence Bundle and Deterministic Ranking 1A

## Boundary

Step 20 consumes the exact immutable Step 18 lexical request/result pairs and
the exact Step 19 vector request/result pair. It does not query a provider,
run a model, retrieve from a database, or decide an answer. The public service
accepts typed upstream contracts only and produces an immutable
`HybridEvidenceOutcome` containing a `FrozenEvidenceBundle` for a valid HAT
route.

The bundle remains bound to the Step 17 `KnowledgeRouteResult` and
`PolicyGateResult`. Its evidence status does not replace the independent
knowledge-policy decision, answer status, or execution-authorization decision.

## Shared identity and verification

One Step 20 request binds:

- request, tenant, user, route hash, selected HAT/version/manifest, HAT scope,
  and effective scope;
- the Step 17 policy result hash;
- every available Step 18 request and result hash;
- the Step 19 vector request/result and approved embedding-model digest;
- Step 18 retrieval-policy identity;
- Step 20 ranking and diversity policy digests;
- the bounded item and context-byte limits.

The service verifies route, policy, request, result, and candidate hashes
before fusion. Every upstream result must have the same shared binding. A
stale hash, an identity mismatch, or a widened scope fails closed. A
`PASS_THROUGH` route returns a deterministic no-HAT/no-bundle outcome;
`AMBIGUOUS` fails closed.

## Hard eligibility revalidation

Step 18 and Step 19 continue to apply SQL hard filters before candidate
generation. Step 20 adds defense-in-depth admission checks for exact tenant,
HAT scope, effective scope, `PUBLISHED` state, supported authority class,
access class, target scope, private owner/personal space, redaction state,
content hash, lineage identity, and vector model digest.

Authority is never a score. Vector proximity, lexical rank, exact match, or
the number of modalities cannot admit an otherwise ineligible source.

## Canonical identity and deduplication

The cross-modality candidate identity is:

```text
(tenant_id, hat_scope_id, source_id, knowledge_version_id, chunk_id,
 content_sha256)
```

Different versions and different content hashes remain separate. Duplicate
identities merge only when all security-semantic metadata agree exactly. A
metadata conflict fails closed. Each contribution preserves modality,
upstream request/result/candidate hashes, one-based modality rank, and the
upstream canonical score/distance string.

## Fixed deterministic fusion

Policy `hybrid-retrieval-ranking-1a`, version `1`, is immutable:

- `RRF_K = 60`;
- `RRF_SCALE = 1,000,000,000`;
- statute/section weight `8`;
- exact identifier weight `8`;
- full text weight `4`;
- vector weight `3`;
- keyword weight `2`.

Each contribution is integer fixed point:

```text
floor(RRF_SCALE * weight / (RRF_K + one_based_rank))
```

Raw lexical scores and vector distances are not compared. Structured
statute/section matches rank before exact identifiers, which rank before
non-exact multimodal, lexical-only, and vector-only candidates. Integer score,
modality count, modality ranks, and immutable lineage fields provide complete
stable tie breaking. Input permutation and duplicate replay cannot inflate or
change the result.

## Diversity and byte budget

Policy `hybrid-diversity-1a`, version `1`, uses deterministic limits:

- 40 bundle items;
- 3 items per source;
- 4 items per knowledge version;
- 8 exact-priority items.

Exact candidates are considered first. Remaining candidates use stable
round-robin selection across source groups ordered by their best fused rank.
All exclusions are counted, and overflow marks the bundle truncated.

Context assembly is provider-neutral and byte-based:

- default budget: 65,536 bytes;
- maximum budget: 262,144 bytes;
- maximum excerpt: 8,192 bytes per item;
- minimum partial excerpt: 256 bytes;
- maximum canonical bundle: 524,288 bytes.

The assembler uses only full content or an exact UTF-8-safe prefix. Each item
preserves full-content SHA-256, byte offsets, excerpt SHA-256, and truncation
state. It creates no summary and performs no token accounting.

## Frozen Evidence Bundle

Every item has a deterministic evidence ID derived from exact candidate
identity and a hash over its ordered contribution, authority, scope,
provenance, ranking, and excerpt metadata. The bundle hash binds ordered item
membership, every upstream result hash, route and policy identities, model
identity, policies, counts, exclusions, budget, coverage, and statuses. It also
stores the exact Step 20 request hash, requested modality set, and the
caller-reduced item limit, so a semantically different bounded request cannot
reuse the same frozen contract. The bundle ID is derived from that hash.

`COMPLETE` means complete only for the bounded requested modalities and Step 20
policy. Missing modalities, upstream truncation, diversity exclusions, or
context truncation yield `PARTIAL`; zero admissible results yield `EMPTY`.
Integrity failure produces no bundle and exposes `INVALID` through the typed
boundary error.

## Persistence decision

No migration is added. The Step 4 `evidence_items`, `evidence_bundles`, and
`evidence_bundle_items` tables retain RLS/FORCE RLS and valid lineage FKs, but
`evidence_bundles` requires an existing `kernel_run_id`. Step 17-19 retrieval
contracts do not carry a verified kernel-run binding. Treating `request_id` as
that foreign key or hiding the missing fact in metadata would weaken identity.

Step 20 therefore freezes the bundle in memory and records persistence as
`NOT_APPLICABLE_STEP4_REQUIRES_EXISTING_KERNEL_RUN_BINDING`. A later authorized
orchestration boundary may persist the same bundle only after supplying an
exact existing kernel run. No competing table is introduced.

## Step 21 handoff and non-goals

Step 21: NOT STARTED.

All structured temporal/version metadata is preserved unchanged for Step 21.
Step 20 does not select current versus historical law, resolve repeal or
supersession, decide question-time applicability, detect legal conflicts, or
assign freshness/staleness. It also performs no model/provider invocation,
cross-encoder reranking, answer generation, approval, or execution.
