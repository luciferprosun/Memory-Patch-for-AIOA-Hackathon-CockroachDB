# ADR-047: Use a remote-database, single-worker, lazy-embedding 4 GB profile

## Status

Proposed. This ADR is based on the exact Step 39 closure commit
`90c2563556fea96ee120b264166640f277677acd`.

The decision is implemented and controlled resource validation passes in the
Step 40 closure worktree. The canonical evidence digest is
`22d49082321b80ceb91eb94aaaf305fb0c20566b863f4803a69e2ff1b7ce5017`.
This ADR becomes Accepted when the single Step 40 closure commit containing it
is reachable from `origin/main`. Step 41 is `NOT STARTED`.

## Context

The measured target host has 3,751 MiB physical memory, not a full 4,096 MiB.
The unchanged lightweight Kernel/UI paths peak below 84 MiB, while the exact
local multilingual E5 runtime peaks near 700 MiB. The existing local
CockroachDB validation topology declares a 640 MiB memory store, 64 MiB cache,
and 128 MiB SQL-memory pool before Go heap and process overhead. A baseline
readiness probe under current pressure did not complete within its existing
bound and cleaned up.

The roadmap already permits remote database, generation, and embedding
services where required by a constrained host. Step 38 proved one coherent
CockroachDB lineage and the approved hosted generation provider; Step 39
proved the Critic is additive and optional. Step 40 must choose a truthful host
shape without weakening any correctness or authority gate.

## Decision

1. Define `memory-patch-4gb-demo-1a` as a constrained demo/runtime profile,
   not production HA. Accept hosts with at least 3,700 MiB detected physical
   memory, reserve at least 700 MiB nominal OS/filesystem headroom, and set
   local runtime idle, steady, peak, and hard-pressure targets to 512, 2,200,
   3,000, and 3,400 MiB respectively.
2. Require CockroachDB as a remote service for the canonical constrained host.
   Start zero local CockroachDB processes and permit no local fallback. Keep
   the existing single-node local topology only for disposable validation and
   label it non-HA.
3. Use one Python web/backend worker for the Kernel, FastAPI/Jinja2/HTMX owner
   UI, request-driven review, audit adapters, and Personal Memory orchestration.
   Do not start a separate frontend runtime.
4. Keep the approved generation model hosted. Start zero local generation
   model processes and retain existing provider timeout, retry, tooling, and
   credential boundaries.
5. Keep the exact Step 19 `intfloat/multilingual-e5-small` model and 384
   dimensions. Create at most one process-level backend lazily on first vector
   use, reduce the configured batch to 8, set E5/OMP/MKL threads to one, and
   disable tokenizer parallelism. Reuse only the verified external-volume
   cache.
6. Disable the optional Critic by default. A disabled, unavailable, malformed,
   or pressure-suppressed Critic remains a healthy core state. The separately
   selected Critic conformance profile retains Step 39's zero-authority limits.
7. Keep ingestion off after prepared corpus and publication readiness verify.
   Retain ingestion code and allow an explicit bounded maintenance run. Keep
   review request driven and audit always enabled.
8. Bound application, Commit Helper, audit, and review database pools at 4, 1,
   1, and 1 connections. Physical process consolidation never merges those
   purpose-bound credentials or capabilities.
9. Bound provider, embedding, Critic, ingestion, review, audit, and export
   queues at 2, 4, 1, 1, 4, 16, and 1. Bound the blocking executor at four
   threads. Do not increase any semantic result or page limit.
10. Treat all cache as derived, non-authoritative, rebuildable external-volume
    state. Cap derived external cache at 2,048 MiB and in-memory cache at 64
    MiB. Never evict canonical knowledge, evidence, Personal Memory, or audit
    state to meet a resource target.
11. Apply pressure degradation in this order: suppress optional Critic, pause
    optional ingestion, backpressure heavy embedding, backpressure large
    exports, then fail a required request closed. Pressure cannot skip
    verification, temporal policy, audit, RLS, human approval, Commit Helper,
    or activation.
12. Use a cheap liveness probe and a typed readiness projection. Critic
    disabled, E5 unloaded, ingestion disabled, and request-driven review are
    intentional healthy states; database/schema, corpus, volume, persistence,
    audit, provider configuration, and owner UI remain required.
13. Bind acceptance to the larger of measured core scenarios plus the complete
    E5 peak. The controlled run measured a conservative 784 MiB peak against a
    3,000 MiB gate, with zero provider calls, production database operations,
    AWS/S3 mutations, authority violations, or secret leakage.
14. Leave the full security/regression campaign, production HA design, AWS
    deployment, RC work, and backup/restore to later steps. Step 41 remains
    `NOT STARTED`.

## Consequences

The constrained host has a large measured safety margin and does not duplicate
the heaviest local runtime. Idle startup is lightweight, while first vector use
pays a measured model-load latency. The application now exposes deterministic
resource and optional-component status instead of treating an intentionally
disabled service as a failure.

The profile depends on network reachability to CockroachDB and the already
approved generation provider. It does not prove a standalone all-local demo or
production HA. Database/server-side and provider/server-side memory are outside
the 4 GB host budget, but their correctness and authority contracts remain
mandatory.

## Rejected alternatives

### Run local CockroachDB and local E5 in the canonical profile

Rejected because the current local CockroachDB configuration declares 832 MiB
before unmeasured process overhead, the baseline readiness probe failed under
host pressure, and Step 40 has no honest combined-RSS proof. The disposable
topology remains available for validation.

### Start several web workers

Rejected because each process can duplicate Python heaps and an E5 instance.
One async-capable worker is sufficient for the bounded demo concurrency.

### Eagerly load E5 at startup

Rejected because it would add roughly 706 MiB and about 52.5 seconds to every
startup even when no vector request is made.

### Change or quantize the embedding model

Rejected because Step 40 is not a model-identity or retrieval-correctness
rewrite. The exact Step 19 identity and vector dimension remain frozen.

### Disable audit, RLS, verification, or Personal Memory gates under pressure

Rejected because resource pressure is diagnostic state, not authority. A
request fails closed before any correctness or security gate is bypassed.

### Merge application and Commit Helper credentials or pools

Rejected because Step 36 separation is an authority boundary, not an
optimization target.

### Count swap as application memory

Rejected because swap availability is host-dependent and can hide unusable
latency or memory pressure. It is recorded for diagnosis only.

### Make Critic mandatory because the bridge exists

Rejected by Step 39. The core Memory Patch flow remains complete without it.
