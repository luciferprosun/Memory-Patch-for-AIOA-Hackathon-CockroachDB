# Step 40 runtime component inventory 1A

## Status and measurement boundary

This inventory is bound to the Step 39 closure base
`90c2563556fea96ee120b264166640f277677acd`. The controlled Step 40 resource
run returned `PASS_4GB_CONTROLLED` with validation digest
`22d49082321b80ceb91eb94aaaf305fb0c20566b863f4803a69e2ff1b7ce5017`.
The final full repository regression and Git closure are recorded separately in
the Step 40 closure record.

The measured host exposes 3,751 MiB of physical memory, two logical CPUs, and
4,063 MiB of swap. Swap is reported for diagnosis only and is not counted as
application capacity. Every measurement used a fixed provider-free scenario,
captured only numeric resource facts and output hashes, and recorded no raw
command, model text, machine path, credential, or database locator.

## Constrained process layout

```text
browser
  -> one FastAPI/Jinja2/HTMX Kernel and owner-UI process
       -> remote required CockroachDB service
       -> hosted approved generation provider
       -> one local E5 instance, loaded on first vector request
       -> verified external-volume derived cache
       -> optional Critic call, disabled by default
```

The diagram is a constrained demo profile, not a production high-availability
topology. Logical database capabilities retain separate purpose-bound
connections even when their adapters execute in the one application process.

## Inventory

| Component | Classification | Process and startup model | Observed resource evidence | Disk, cache, and network | Lazy or disable decision | Restart behavior |
|---|---|---|---|---|---|---|
| Knowledge Kernel and web backend | `REQUIRED_FOR_DEMO`, `REQUIRED_FOR_CORE_RUNTIME` | One Python process and one web worker; deterministic startup after profile, volume, database, schema, and corpus preflight | Idle import: 65 MiB peak RSS, one process, one thread, 4.461 s | Reads repository contracts; connects to CockroachDB and the approved hosted provider | Cannot be disabled; one worker prevents duplicate heaps and model copies | Restart process; readiness remains false until all required dependencies pass |
| Owner UI and static serving | `REQUIRED_FOR_DEMO`, `REQUIRED_FOR_CORE_RUNTIME` | FastAPI, Jinja2, and vendored HTMX in the same backend process; no React, Vue, Next, or production Node process | UI test path: 71 MiB peak RSS, one process, three peak threads, 6.451 s | Vendored static asset; no CDN runtime dependency | Cannot be disabled in the demo; no second frontend process | Restart with the backend; server-side auth and session policy remain unchanged |
| CockroachDB system of record | `REQUIRED_FOR_DEMO`, `REQUIRED_FOR_CORE_RUNTIME` | Required remote service for the canonical 4 GB host profile; zero local CockroachDB processes | Local host RSS and connections are zero by placement. Step 38 remains the exact one-database live correctness proof. A pre-change local 640 MiB store, 64 MiB cache, and 128 MiB SQL-memory probe did not become ready within its existing bound under host pressure and cleaned up fully | Network dependency; authoritative data remains in CockroachDB | Cannot be disabled. Local single-node remains an available validation topology only and is not production HA | Reconnect through bounded, purpose-separated pools; no embedded automatic local fallback |
| Hosted generation provider adapter | `REQUIRED_FOR_DEMO`, `REQUIRED_FOR_CORE_RUNTIME` | In-process bounded HTTP adapter; no local generation-model process | Included in the 65-78 MiB non-embedding scenario envelope; live provider use is anchored to Step 38, while Step 40 resource validation made zero provider calls | Approved hosted network dependency; no model weights stored locally | Cannot be replaced or silently degraded into a local model | Existing bounded timeout and retry contracts apply; no retry expansion in Step 40 |
| Local multilingual E5 | `REQUIRED_FOR_DEMO` when vector retrieval is invoked; `REQUIRED_FOR_CORE_RUNTIME` on vector paths | Exactly one process-level instance behind a thread-safe lazy manager | 706 MiB peak RSS, one process, one thread; 52.499 s load, 0.861 s first query, 0.231 s repeated query; the same instance and vector result were reused | Exact Step 19 model and dimension; verified external-volume cache; zero network calls | Unloaded at idle, loaded on first permitted vector request; batch 8; cannot be duplicated across workers | Failed load leaves no instance; a later bounded request may retry under existing error semantics |
| Hybrid retrieval and temporal/evidence pipeline | `REQUIRED_FOR_DEMO`, `REQUIRED_FOR_CORE_RUNTIME` | In-process request path; no standing retrieval worker | Retrieval-only: 76 MiB peak RSS, one process, one thread, 8.293 s | Uses CockroachDB and the E5 cache/runtime when vector work is needed | Cannot be disabled for the German Law path | Request failure remains fail closed and creates no authority |
| Personal Memory persistence, audit, approval, activation, and active retrieval | `REQUIRED_FOR_DEMO`, `REQUIRED_FOR_CORE_RUNTIME` | Request-driven adapters in the application process with separate logical database purposes | Step 30 and Step 31 scenario: 40 MiB peak RSS, one process, one thread, 18.810 s | CockroachDB network dependency; audit remains append-only and hash chained | Cannot be disabled. Commit Helper capability stays separate even though there is no permanent helper worker | Exact transactional retry, idempotency, audit, and human approval contracts remain unchanged |
| Review workspace | `REQUIRED_FOR_CORE_RUNTIME` on review requests | Request-driven in the backend; zero always-on review workers | Included in the application envelope; separately covered by Step 34 regressions | CockroachDB dependency with its own bounded pool | Not disabled, but no idle worker is started | A failed request remains review-only and grants no reviewer authority |
| Critic Prompt Loop bridge | `OPTIONAL` | No persistent worker; optional provider call through Step 39 bridge | Disabled check: 23 MiB peak RSS. Enabled provider-free conformance: 159 MiB peak RSS and two transient test processes. Conservative enabled-plus-E5 total: 865 MiB | Hosted provider only if explicitly enabled; no local Critic model | Disabled by default. Pressure guard suppresses it before core work | Unavailable, malformed, timed-out, or pressure-rejected Critic never blocks the core Memory Patch path |
| Ingestion and publication workers | `OFFLINE_OR_MAINTENANCE_ONLY` after the validated corpus is prepared | Zero workers in the 4 GB profile; existing ingestion code is retained | No idle RSS in the constrained profile | Uses verified external volume and CockroachDB only when explicitly run | Disabled by default only after corpus/publication readiness succeeds | Operator enables a bounded maintenance run; failure cannot invalidate an already verified publication state |
| Audit export, Personal Memory export, and reports | `REQUIRED_FOR_CORE_RUNTIME` when requested | Request-driven, bounded result and export queues; no standing worker | Included in the application envelope | Canonical streaming or bounded serialization; no in-memory unbounded cache | Large work is backpressured under pressure; audit itself is never disabled | A rejected export changes no canonical state |
| External-volume runtime adapter and caches | `REQUIRED_FOR_DEMO`, `REQUIRED_FOR_CORE_RUNTIME` | In-process preflight and bounded file operations | Probe is included in startup; no permanent worker | Verified external volume; derived, non-authoritative, rebuildable cache; system-drive fallback forbidden | Cannot disable the volume preflight. Derived cache may be rebuilt or evicted within its 2,048 MiB bound | Identity change fails readiness rather than falling back |
| CLI database transport, migration runners, corpus acquisition, and validation fakes | `TEST_ONLY` or `OFFLINE_OR_MAINTENANCE_ONLY` | Short-lived owned children with exact cleanup | Excluded from canonical steady-state process count; their focused tests are retained | Disposable local resources only | Not started by the 4 GB runtime profile | Existing exact-PID cleanup and no-orphan gates remain authoritative |

## Baseline before Step 40 changes

Before implementation, the host had 1,016 MiB available, 249 processes, and
818 threads. The unchanged code measured 65,640 KiB for idle core imports,
76,836 KiB for retrieval-only, 78,596 KiB for the Step 38 provider-free module,
40,220 KiB for approval/activation plus active retrieval, 83,148 KiB for the
Step 39 bridge lane, and 72,072 KiB for the owner UI. The local E5 measurement
was 694,432 KiB peak RSS. These are point-in-time observations, not minimum
hardware promises.

The failed idle local-CockroachDB readiness probe is retained as a baseline
fact, not converted into an RSS claim. Its owned process and temporary store
were removed. The constrained profile therefore does not pretend that an
unmeasured local CockroachDB plus E5 is safe on this host; it makes the database
a required remote dependency and retains the prior Step 38 live database proof.

## Duplicate runtime and unbounded concurrency review

- Web workers: one.
- Local generation-model processes: zero.
- Local E5 instances: one maximum, lazily created.
- Persistent Critic workers: zero.
- Persistent ingestion and review workers: zero.
- Local CockroachDB processes in the core profile: zero.
- Provider, embedding, Critic, ingestion, review, audit, and export queues all
  have explicit positive maxima.
- The blocking executor is capped at four threads; E5 intra-op, OMP, and MKL
  thread counts are one; tokenizer parallelism is disabled.
- The validation-only interactive CockroachDB CLI queue is not part of the
  production profile. Its subprocess lifetime and result sizes remain bounded
  by existing validation contracts.

No Step 41 security campaign or production AWS deployment is part of this
inventory.
