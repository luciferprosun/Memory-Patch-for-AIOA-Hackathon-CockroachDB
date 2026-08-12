# Memory Patch System Overview 1A

## Product boundary

Memory Patch is a domain-neutral Knowledge Kernel whose first complete client
is the German Law HAT. It does not ask a model to certify itself. Instead, it
keeps generation, evidence, correction, approval, private memory, and review
as separate typed authority domains.

The release-candidate runtime described here is frozen by the [Step 42 RC
manifest](../evidence/release/step42-rc-manifest-1a.json). Step 43 adds only
documentation and demo orchestration; it does not change schema, provider,
model, prompts, evidence policy, or authority semantics.

## Verified-answer path

```text
User
  -> Step 35 UI/API (presentation, authentication, owner actions)
  -> Axis A Router (route and HAT scope)
  -> Axis B Policy (answer/retrieval/fail-closed decision)
  -> Steps 18-20 exact/full-text/vector/hybrid retrieval
  -> Evidence Bundle (source, version, span, lineage, authority)
  -> Step 21 temporal/conflict/freshness resolver

Original question
  -> Step 22 Draft V1 (evidence blind, tools/web/code disabled)
  -> Step 23 claim extraction and evidence binding
  -> Step 24 immutable Correction Packet + integrity receipt
  -> Step 25 evidence-bound Draft V2 + layered verifier
  -> Step 26 Verified Answer OR explicit fail-closed result
```

Draft V1 receives the original query and pinned provider policy only. It has no
retrieval results, Evidence Bundle, Correction Packet, database credential,
Commit Helper credential, browser tool, web tool, code tool, or external-action
port. Draft V2 receives only the bounded, typed correction context. Step 26
reconstructs the complete route/policy/evidence/packet/verification lineage
before deciding whether any answer may be returned.

## Private Personal Memory path

```text
Verified correction
  -> Step 28 Correction Candidate (DETECTED)
  -> Step 29 Proposal + evidence validation
  -> AWAITING_APPROVAL
  -> explicit authenticated owner approval
  -> Step 30 least-privileged technical Commit Helper
  -> COMMITTED -> ACTIVE
  -> Step 31 later owner-scoped retrieval
  -> compatible cross-model context reuse
```

The owner approves; the Commit Helper performs only a hash-bound technical
commit. A model, Critic, reviewer, browser field, or service identity cannot
manufacture approval. ACTIVE Personal Memory remains private and has
`canonical_evidence_authority=false`. Canonical conflict suppresses memory
rather than letting memory override evidence. Steps 32–34 preserve lifecycle,
audit, and human-review boundaries.

## Optional Critic path

```text
Bounded model-answer artifacts + permitted verified context
  -> Step 39 Critic assessment
  -> typed candidate-only envelope
  -> Step 28 intake
  -> normal Step 29 validation and Step 30 owner/commit gates
```

The Critic is disabled by default in the 4 GB profile. When enabled, it can
identify an issue and propose a candidate. It has no canonical-evidence,
route, source, review, approval, commit, activation, execution, external-action,
tenant, or owner authority. Failure or malformed output does not block the
core verified-answer path.

## Authority matrix

| Component | May produce text/candidate | Canonical evidence | Owner approval | Technical commit | External action |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hosted model / Draft V1 | Yes | No | No | No | No |
| German Law HAT | Retrieval/policy only | Selects eligible registered evidence | No | No | No |
| Layered verifier | Verdict only | Validates, does not invent | No | No | No |
| Critic | Candidate only | No | No | No | No |
| Authenticated owner | Owner decision | No | Yes, own proposal only | No | No |
| Commit Helper | No | No | No | Yes, exact approved receipt only | No |
| Human reviewer | Typed review decision | No | No | No | No |
| Browser/UI | Presentation and owner request | No | No independent authority | No | No |

## CockroachDB role

CockroachDB v26.2.4 is the durable system of record for source registry and
publication lineage, chunk/retrieval foundations, workflow/idempotency state,
Personal Memory slots/proposals/approvals/commits/lifecycle, audit events and
chain heads, and review state. RLS and `FORCE RLS` bind tenant/owner access.
Purpose-specific roles keep migration, application, publication, Commit
Helper, reviewer, and audit capabilities separate. Step 42 validated native
backup and isolated restore without broadening those grants.

## Constrained runtime

The [Step 40 profile](../evidence/performance/step40-4gb-resource-validation.json)
uses one web/backend worker, hosted generation, one lazy process-level local E5
runtime, bounded embedding batch/queues/threads/DB pools, external-volume
caches, request-driven review/audit functions, and optional Critic/ingestion
disabled by default. Pressure rejects optional/heavy work before core
verification and never bypasses RLS, audit, approval, or evidence rules.

## Evidence and presentation

The judge demo uses two explicitly different modes:

- LIVE observes at most two new evidence-blind Draft V1 responses after an
  explicit cost authorization.
- REPLAY validates the committed 12-stage hash-only Step 38 trace and frozen
  Step 42 live proof. It is not described as a new provider validation.

The complete entry point is the [Golden Path](../demo/MEMORY_PATCH_DEMO_GOLDEN_PATH_1A.md),
and the strongest engineering records are indexed in the [submission
index](../SUBMISSION_INDEX_1A.md).
