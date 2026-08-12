# Step 40 4 GB resource budget 1A

## Decision

The canonical Step 40 target is a constrained demo host with nominal 4,096 MiB
RAM and a minimum detected physical-memory gate of 3,700 MiB. The measured host
provides 3,751 MiB. The profile is deliberately stricter than the prompt's
initial example because the actual core footprint is much smaller:

| Budget | Frozen value |
|---|---:|
| Nominal host RAM | 4,096 MiB |
| Minimum accepted detected RAM | 3,700 MiB |
| OS, kernel, and filesystem headroom | at least 700 MiB |
| Idle local runtime target | 512 MiB |
| Steady local runtime target | 2,200 MiB |
| Peak local runtime target | 3,000 MiB |
| Hard observed-host-usage pressure threshold | 3,400 MiB |
| Minimum available-memory pressure threshold | 384 MiB |

Swap is diagnostic only. It does not increase a budget, turn a failure into a
pass, or justify silent thrashing.

## Component allocations

These allocations are safety envelopes, not claims that every component owns a
separate process or reserves the full amount.

| Local component envelope | Budget | Placement and rationale |
|---|---:|---|
| Kernel, FastAPI, Jinja2, HTMX, owner UI, routing, retrieval, Personal Memory, audit, and review adapters | 256 MiB | One process and one web worker. Observed non-embedding peaks were 23-78 MiB. |
| One lazy multilingual E5 instance, tokenizer, tensors, and first-call transient | 900 MiB | Exact Step 19 identity; observed 706 MiB peak. No duplicate worker copy. |
| Provider client, request/response bounds, verification, and canonical serialization transients | 128 MiB | Hosted generation means no local generation-model weights. |
| Bounded in-memory caches and all bounded queues | 128 MiB | Profile cache ceiling is 64 MiB; remaining envelope covers queue and object overhead. |
| Personal Memory, audit, review, and Commit Helper request transients | 256 MiB | Logical capabilities may share the application process, but credentials and database pools remain separate. |
| Golden-path and operating variance reserve | 512 MiB | Covers allocator variance, imports, page tables, and bounded request overlap. |
| Local CockroachDB in the canonical core profile | 0 MiB | CockroachDB is a required remote service. There is no silent local fallback. |
| Persistent Critic, ingestion, review, or frontend worker | 0 MiB | Critic and ingestion are off by default; review is request driven; static UI is served by the backend. |

The explicit envelopes total 2,180 MiB, below the 2,200 MiB steady target. The
3,000 MiB peak gate adds 820 MiB transient margin while still preserving at
least 700 MiB nominal host headroom.

## Measured acceptance

The committed controlled run is
`docs/evidence/performance/step40-4gb-resource-validation.json`. Its canonical
validation digest is
`22d49082321b80ceb91eb94aaaf305fb0c20566b863f4803a69e2ff1b7ce5017`.

- Largest measured non-embedding core scenario: 78 MiB peak RSS.
- Real E5: 706 MiB peak RSS, one instance, one thread, batch 8.
- Conservative core formula: largest non-embedding scenario plus the complete
  E5 peak.
- Conservative core peak: 784 MiB.
- Configured acceptance peak: 3,000 MiB.
- Measured safety margin to configured peak: 2,216 MiB.
- Optional Critic conformance plus E5: 865 MiB, also within the peak budget,
  although Critic remains disabled by default.

The conservative sum intentionally double-counts some Python/runtime overhead
because the scenarios were measured in separate child processes. It is safer
than subtracting a guessed shared baseline and remains far below the gate.

## Database budget and topology

CockroachDB remains mandatory, authoritative, and external to the constrained
host process budget. The local client pool ceilings are:

| Purpose | Maximum connections |
|---|---:|
| Application | 4 |
| Commit Helper | 1 |
| Audit append/read adapter | 1 |
| Review adapter | 1 |
| Total configured client ceiling | 7 |

The pools remain logically and credential-wise separate. They are not merged
to save memory. Step 40 touched no production database and therefore makes no
claim about an observed production connection count. It records zero local
connections and verifies the bounded configuration. Step 38 remains the
one-runtime, one-database coherent live proof.

The repository's local single-node validation topology remains available with
the pre-existing 640 MiB in-memory store, 64 MiB cache, and 128 MiB SQL-memory
configuration. Those configured values total 832 MiB but are not RSS. A
pre-change readiness probe under host pressure failed within the existing
bound and cleaned up. Consequently, Step 40 does not claim that local
CockroachDB plus local E5 is a validated 4 GB core topology, and it does not
claim production HA.

## Queue, thread, and cache budgets

| Bound | Value |
|---|---:|
| Provider queue | 2 |
| Embedding queue | 4 |
| Critic queue | 1 |
| Ingestion queue | 1 |
| Review queue | 4 |
| Audit queue | 16 |
| Export queue | 1 |
| Blocking executor threads | 4 |
| E5 intra-op threads | 1 |
| OMP threads | 1 |
| MKL threads | 1 |
| Tokenizer parallelism | disabled |
| Derived external cache | 2,048 MiB maximum |
| In-memory cache | 64 MiB maximum |

Cache is `EXTERNAL_DERIVED`, non-authoritative, rebuildable, and stored only on
the verified external volume. Canonical evidence and authoritative database
state are never evicted as a resource shortcut.

## Pressure policy

At soft or hard pressure, the order is fixed:

1. suppress optional Critic work;
2. pause optional ingestion;
3. backpressure new heavy embedding work;
4. backpressure large export/report work;
5. fail a required request closed if hard pressure remains.

The guard never skips verification or temporal resolution, disables audit or
RLS, changes route/source authority, returns a known-bad Draft V1, or approves,
commits, or activates Personal Memory. Queue saturation applies backpressure
at the declared limit even when memory is otherwise normal.

## Scope boundary

This budget validates a constrained demo/runtime profile. It is not a
production HA sizing result, a provider SLA, an AWS deployment, or the Step 41
full security campaign. Step 41 remains `NOT STARTED`.
