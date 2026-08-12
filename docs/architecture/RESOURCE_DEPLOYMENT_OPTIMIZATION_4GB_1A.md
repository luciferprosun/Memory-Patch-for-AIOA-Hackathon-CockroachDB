# Resource and deployment optimization for 4 GB 1A

## Status

Implemented and controlled-validated in the Step 40 closure worktree, based on
the exact Step 39 closure commit
`90c2563556fea96ee120b264166640f277677acd`. The resource evidence status is
`PASS_4GB_CONTROLLED`, its canonical digest is
`22d49082321b80ceb91eb94aaaf305fb0c20566b863f4803a69e2ff1b7ce5017`,
and the conservative core peak is 784 MiB against a 3,000 MiB limit. Final Git
closure is recorded in the Step 40 closure record. Step 41 is `NOT STARTED`.

## Architectural invariant

Step 40 changes resource placement and limits, not semantics or authority. The
following remain byte- and policy-authoritative in their existing layers:

- route and HAT selection;
- source publication and canonical evidence authority;
- hybrid retrieval and temporal/conflict/freshness policy;
- Correction Packet, Draft V2, verifier, and Verified Answer eligibility;
- Personal Memory proposal, human approval, Commit Helper, commit, and
  activation boundaries;
- RLS, owner/tenant isolation, audit hash-chain semantics, review
  authorization, credential separation, and failure recovery.

No resource observation, queue state, RSS threshold, cache hit, or disabled
optional service can grant authority.

## 4 GB deployment shape

The canonical profile is `config/runtime/4gb-demo-1a.json`, with profile ID
`memory-patch-4gb-demo-1a`, version `1.0.0`, and digest
`eb205843f34039418bd49663ab47390777cbdc861f671172c8f025d588a55ad8`.

```text
3.7-4.0 GiB constrained host
  one Python Kernel/UI web worker
    routing, retrieval, evidence, verification
    Personal Memory, audit, review, server-rendered UI
    one lazy local E5 singleton
    bounded provider, embedding, audit, review, export queues
  verified external-volume derived cache
  remote required CockroachDB service
  hosted approved generation provider
  optional hosted Critic invocation, disabled by default
```

This is a `CONSTRAINED DEMO PROFILE NOT PRODUCTION HA`. A local CockroachDB
single-node runtime remains useful for disposable validation, but it is not
started by this profile and is not presented as production HA.

## Required and optional components

Required components are the backend/UI, CockroachDB connectivity and schema,
prepared German Law corpus, retrieval/correction/verification, Personal
Memory, audit, owner authorization, verified external volume, and approved
provider configuration. The generation model is hosted; Step 40 introduces no
local generation model.

The Critic is additive and disabled by default. Its disabled or unavailable
state is healthy for core readiness. Ingestion workers are off after the
prepared corpus and publication state verify. Review remains available through
request-driven adapters without an always-on worker. Audit is never optional.

## Process consolidation without credential consolidation

One backend process serves the Kernel API, Jinja2 pages, and vendored HTMX
assets. This avoids a second frontend runtime and duplicated Python heaps.
Review and audit adapters may execute in this process, but their purpose-bound
database runners remain distinct. The application, Commit Helper, audit, and
review connection ceilings are respectively 4, 1, 1, and 1. Step 36 capability
separation remains intact; process colocation never implies credential sharing.

There are zero persistent local generation-model, Critic, ingestion, or review
worker processes. Provider concurrency is two and the blocking executor is
capped at four threads.

## Embedding loading and batching

`LazyEmbeddingRuntime` owns an injected backend factory and uses a lock to
create exactly one instance on the first permitted request. Merely importing
or starting the backend leaves E5 unloaded. Concurrent first requests converge
on the same object; failed or pressure-rejected loads do not increment the
instance count.

The identity remains `intfloat/multilingual-e5-small`, 384 dimensions, exact
Step 19 revision and verified files. The batch is reduced from the historical
default 32 to 8 without changing the Step 19 hard maximum 64. The constrained
child environment sets OMP, MKL, and E5 intra-op concurrency to one and disables
tokenizer parallelism. These are process-local settings, not global system
mutation.

The controlled run measured one process, one thread, 706 MiB peak RSS, 52.499 s
load, 0.861 s first query, and 0.231 s repeated query. The second query reused
the one backend and returned the same vector bytes. The model and derived cache
remain on the verified external volume; no network call was made.

## Cache and external volume

Derived model, tokenizer, vector, and temporary cache data use one verified
external-volume class. The profile caps the derived external cache at 2,048
MiB and in-memory cache at 64 MiB. It permits eviction or rebuilding only for
derived data. Canonical corpus evidence, publication state, Personal Memory,
and audit facts cannot be discarded to meet a memory target. System-drive
fallback remains forbidden.

## Bounded queues, threads, and results

The profile declares queue maxima: provider 2, embedding 4, Critic 1, ingestion
1, review 4, audit 16, and export 1. Saturation causes backpressure or safe
rejection. The profile does not increase any existing retrieval, Evidence
Bundle, patch, audit export, review queue, or owner UI page limit.

Large corpus processing remains streaming/spooled as established earlier.
Step 40 does not introduce a second pretty/canonical JSON copy of large
semantic records in core code. The measurement and evidence scripts keep only
bounded output hashes and numeric metrics.

## Resource pressure guard

`ResourcePressureGuard` accepts a hash-bound host/process observation and one
closed work kind. It derives `NORMAL`, `SOFT_PRESSURE`, or `HARD_PRESSURE` from
the versioned profile. Its decisions are immutable and hash-bound.

The fail-safe order is optional Critic, optional ingestion, heavy embedding,
large export, then required-request failure. A hard-pressure embedding test
proved zero backend factory calls, zero partial state, and zero duplicate side
effects. Every decision fixes verifier, audit, RLS, route, source authority,
canonical evidence, and automatic approval override flags to false.

## Startup, readiness, liveness, and status

Startup order is exact:

1. validate the profile and digest;
2. verify external-volume identity and reserve;
3. connect to the required CockroachDB service;
4. verify schema;
5. verify the prepared German Law corpus/publication state;
6. start the single Kernel/UI worker;
7. keep E5 unloaded until a vector request;
8. keep Critic disabled unless explicitly selected;
9. keep ingestion disabled unless an operator starts maintenance.

Readiness projects already-performed cheap dependency checks. Database/schema,
corpus, Personal Memory persistence, audit append, volume, provider config, and
owner UI are required. Critic disabled, E5 unloaded, ingestion disabled, and
request-driven review are explicit healthy states. Liveness checks process
responsiveness only; it performs no model call, full E2E, retrieval campaign,
or audit-chain verification.

## Correctness and resource evidence

The controlled scenarios measured:

| Scenario | Peak RSS | Duration | Result |
|---|---:|---:|---|
| Idle Kernel/UI imports | 65 MiB | 4.461 s | PASS |
| German Law retrieval-only | 76 MiB | 8.293 s | PASS |
| Step 38 provider-free German Law core | 78 MiB | 11.136 s | PASS |
| Personal Memory approval/activation plus active retrieval | 40 MiB | 18.810 s | PASS |
| Critic disabled | 23 MiB | 1.190 s | PASS |
| Critic enabled provider-free conformance | 159 MiB | 29.795 s | PASS |
| Owner UI | 71 MiB | 6.451 s | PASS |

The conservative core peak is the largest non-embedding scenario plus the
complete E5 peak: 78 + 706 = 784 MiB. This is below the frozen 3,000 MiB peak
gate by 2,216 MiB. The live nondeterministic provider and same-database
Personal Memory lineage are not re-manufactured: their exact authority and
hashes remain anchored to the committed Step 38 live evidence, while Step 40
runs provider-free correctness regressions.

## Known limitations

- The resource run did not access a production database or observe its remote
  connection count. It verified the local pool ceiling of seven and zero local
  connections/processes.
- The canonical profile requires remote CockroachDB and a hosted generation
  provider; their server-side memory is not part of the constrained host.
- Local single-node CockroachDB plus local E5 is not claimed to meet the 4 GB
  gate.
- The E5 first-load latency is approximately 52.5 seconds on the measured host;
  subsequent inference is much faster. No SLA is frozen by Step 40.
- The Critic-enabled result uses the mandatory provider-free Step 39 adapter;
  real Critic provider validation remains `UNAVAILABLE_NOT_REQUIRED`.
- Step 40 is not production HA, AWS deployment, RC freeze, backup/restore, or a
  full security campaign.

## Step 41 boundary

Step 41 owns the full security and regression campaign. Step 40 performs only
the required isolation, Commit Helper, browser boundary, audit, Critic
optionality, and recovery spot checks. `Step 41: NOT STARTED`. Step 40
completion does not authorize Step 41 implementation.
