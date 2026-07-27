# Memory Patch for AIOA — Step 3 Closure Record

## 1. Status

`COMPLETE WITH EXPLICIT DEFERRED ITEMS`

The exact CockroachDB runtime was verified, the mandatory live matrix was
executed, the immutable version pin and sanitized evidence were generated, and
all probe-owned runtime resources were removed. The capability result is
`34 PASS / 0 FAIL / 2 DEFER`.

## 2. Step Identity

- Step: `Step 3 — CockroachDB v26.2 Capability Spike and Version Pin 1A`
- Required starting HEAD:
  `2729b06748dfb0c6306e20ef60246ae97510a204`
- Canonical predecessor:
  `Step 2 — Kernel Contract Re-Audit and Authority Invariant Closure 1A`
- Selected runtime: `CockroachDB CCL v26.2.4`
- Cluster version: `26.2`
- Branch: `main`
- Runtime plane: `DISPOSABLE_LOCAL_SINGLE_NODE`
- Evidence generation time: `2026-07-27T04:26:52Z`
- Harness version: `1.0.3`

This record intentionally does not contain the Step 3 Git commit hash. A
commit cannot reliably contain a self-reference to its own final SHA; the
execution report records that hash after commit and push closure.

## 3. Purpose and Boundary

Step 3 established a reproducible CockroachDB capability baseline for later
Memory Patch persistence design. It selected and verified an exact supported
v26.2 patch, exercised the required SQL semantics on that exact live runtime,
recorded sanitized machine-readable evidence, and converted the observations
into explicit architecture constraints.

This was a capability spike. It did not create the production Memory Patch
schema, migrations, tenant tables, persistence adapters, production RLS
policies, authentication, authorization, or retry middleware. Step 4 was not
begun.

## 4. Version Decision

The selected pin is `v26.2.4`, a Regular-channel, public self-hosted GA release
under maintenance support at verification time. It supersedes the preliminary
`v26.2.3` candidate because it is the newest supported v26.2 production patch
with both a public Linux amd64 self-hosted artifact and a separately published
vendor checksum.

The numerically newer `v26.2.5` release was not selected because, at
verification time, it was limited to select CockroachDB Cloud clusters and did
not provide the required public self-hosted artifact.

Verified artifact identity:

- archive SHA-256:
  `3c7de055c07f9101eb0f71b3f5e6b489b0fcf449d3d5a55bfe61eff4f935ce8f`;
- executable SHA-256:
  `a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf`;
- vendor checksum: available and verified;
- build tag: `v26.2.4`;
- platform: `linux`;
- architecture: `amd64`;
- upgrade finalization: verified.

## 5. Capability Result

The canonical matrix contains 36 ordered rows:

- PASS: 34;
- FAIL: 0;
- DEFER: 2;
- overall decision: `PINNED_WITH_EXPLICIT_DEFERRED_ITEMS`;
- matrix SHA-256:
  `b50f52d5985e9eccf44f9c63fb3edf9b2ee002907e20cf646262bdf9b6c485cf`.

PASS rows:

- `CRDB-001`, `CRDB-002`, `CRDB-003`, `CRDB-004`, `CRDB-005`,
  `CRDB-006`, `CRDB-007`, `CRDB-008`, `CRDB-009`, `CRDB-010`;
- `CRDB-011`, `CRDB-012`, `CRDB-013`, `CRDB-014`, `CRDB-015`,
  `CRDB-016`, `CRDB-017`, `CRDB-018`, `CRDB-019`, `CRDB-020`;
- `CRDB-021`, `CRDB-022`, `CRDB-023`, `CRDB-024`, `CRDB-025`,
  `CRDB-026`, `CRDB-027`, `CRDB-028`, `CRDB-029`;
- `CRDB-031`, `CRDB-032`, `CRDB-033`, `CRDB-034`, `CRDB-036`.

FAIL rows:

- none.

DEFER rows:

- `CRDB-030 — Natural contention retry signal`: ten bounded, synchronized
  SERIALIZABLE contention attempts completed without a client-visible
  SQLSTATE `40001`; transparent retries produced the valid final value `2`.
  The separate session-local synthetic signal probe (`CRDB-031`) proved
  SQLSTATE `40001`, and the offline classifier (`CRDB-032`) proved bounded
  retry classification. A natural client-visible signal must be revalidated
  on a later production-like cluster.
- `CRDB-035 — TTL/changefeed interaction`: TTL physical deletion,
  sinkless changefeed, and insert/update/delete event probes independently
  passed. The combined observation of a TTL-generated delete in a live
  changefeed was not forced in this bounded run. Official v26.2 behavior says
  such DELETE events are emitted unless disabled; a combined production-like
  probe remains required.

## 6. Principal Live Findings

- Client and server both reported `v26.2.4`; the cluster version was `26.2`.
- Fixed-dimension vector storage, Euclidean distance, cosine distance,
  inner-product ordering, malformed-vector rejection, vector-index creation,
  prefix filtering, and nearest-neighbor correctness passed.
- Vector index creation was tested on an empty disposable table. Prefix
  columns constrain query scope and index eligibility but are not
  authorization.
- English and German full-text search, deterministic matches, inverted
  indexes, and malformed-query rejection passed on neutral synthetic data.
- RLS SELECT/INSERT/UPDATE/DELETE isolation passed for distinct non-admin
  roles. FORCE RLS changed table-owner behavior as expected. Root,
  administrative, ownership, backup, replication, and CDC boundaries remain
  explicit.
- Row-Level TTL DDL and job registration passed. Within the bounded 120-second
  observation window, the expired synthetic row was removed and the
  non-expired row remained.
- Sinkless changefeed execution decoded five synthetic events, including the
  initial scan and resolved progress. The live RLS/changefeed limitation
  returned SQLSTATE `0A000`; CDC must not be treated as automatically
  RLS-filtered.
- Partial uniqueness passed, and the duplicate current row returned SQLSTATE
  `23505`. That permanent uniqueness error is distinct from retryable SQLSTATE
  `40001`.
- AS OF SYSTEM TIME returned the historical value and its invalid-use probe
  returned SQLSTATE `22023`. Historical visibility remains bounded by retained
  MVCC history and does not replace durable audit storage.
- SERIALIZABLE was the observed default. READ COMMITTED availability does not
  change the project baseline.

## 7. Canonical Evidence and Tooling

- Version pin:
  `config/cockroachdb/version-pin.json`
- Runtime fingerprint:
  `docs/evidence/cockroachdb-v26-2/runtime-fingerprint.json`
- Capability matrix:
  `docs/evidence/cockroachdb-v26-2/capability-matrix.json`
- Architecture baseline:
  `docs/architecture/COCKROACHDB_V26_2_CAPABILITY_BASELINE_1A.md`
- Version-pin ADR:
  `docs/adr/ADR-010-cockroachdb-v26-2-version-pin.md`
- Probe harness:
  `scripts/run_cockroachdb_capability_spike.py`
- Ordered SQL probe assets:
  `sql/cockroachdb/capability_spike/`
- Offline tests:
  `tests/test_cockroachdb_capability_spike.py`

The machine-readable evidence contains synthetic identifiers only. It omits
SQL connection strings, credentials, certificates, cloud identifiers, and raw
runtime logs.

## 8. Executed Validation Evidence

The clean Step 2 baseline was validated before Step 3 changes:

- `python3 scripts/validate_contracts.py`
  - result: PASS.
- `python3 -m unittest discover -v`
  - result: PASS;
  - tests: 211;
  - failures: 0;
  - errors: 0.

The final live evidence was generated by:

```text
python3 scripts/run_cockroachdb_capability_spike.py --run --allow-live --runtime-mode local --cockroach-binary /tmp/mp-step3-runtime.hkx1Ed/cockroach-v26.2.4.linux-amd64/cockroach --json-output /tmp/mp-step3-live-result-final-v103.json --evidence-dir docs/evidence/cockroachdb-v26-2
```

Result:

- command: PASS;
- exact runtime: `v26.2.4`;
- mandatory rows: 36;
- PASS: 34;
- FAIL: 0;
- DEFER: 2;
- harness/infrastructure error: none;
- cleanup error: none.

The generated evidence was then validated offline:

- `python3 scripts/run_cockroachdb_capability_spike.py --offline-validate`
  - result: PASS;
  - rows: 36;
  - exact runtime: `v26.2.4`;
  - matrix digest: verified.
- `python3 -m compileall -q scripts tests`
  - result: PASS.
- `python3 -m unittest tests.test_cockroachdb_capability_spike -v`
  - result: PASS;
  - tests: 60;
  - failures: 0;
  - errors: 0.
- `python3 scripts/validate_contracts.py`
  - result: PASS.
- `python3 -m unittest discover -v`
  - result: PASS;
  - tests: 271;
  - failures: 0;
  - errors: 0.

The focused Step 3 suite includes deterministic local-link, canonical JSON,
matrix completeness, redaction, runtime safety, cleanup ownership, and roadmap
closure checks. Live probes are opt-in and do not run during ordinary test
discovery.

## 9. Cleanup and Setting Restoration

The final live run used loopback-only insecure test mode and a bounded
in-memory store:

- listener and HTTP bindings: `127.0.0.1`;
- store: `type=mem,size=640MiB`;
- cache: `64MiB`;
- maximum SQL memory: `128MiB`;
- external I/O: disabled;
- measured runtime: `419.911` seconds.

Cleanup evidence confirms:

- the exact server PID exited;
- every owned child process exited;
- the sinkless changefeed process exited;
- no forced process kill was used;
- all three dynamic listener ports closed;
- the disposable database was absent;
- all disposable roles were absent;
- the TTL schedule was absent;
- no changefeed remained;
- all SQL resources were removed;
- the exact temporary path was removed;
- no cleanup error occurred.

Setting restoration was exact:

| Setting | Original | Restored |
|---|---:|---:|
| `feature.vector_index.enabled` | `t` | `t` |
| `kv.rangefeed.enabled` | `f` | `f` |

No production or shared database was accessed. No real user, tenant, legal,
memory, or personal data was used.

## 10. Step 4 Constraints

Step 4 must:

- use exactly the pinned `v26.2.4` runtime unless a separate reviewed evidence
  update changes the pin;
- preserve SERIALIZABLE as the baseline and implement bounded handling for
  SQLSTATE `40001`;
- treat SQLSTATE `23505` as a permanent uniqueness result, not a retry signal;
- use tenant and HAT vector prefixes only as query/index dimensions, never as
  authorization;
- test RLS and FORCE RLS using non-admin roles and preserve explicit
  administrative bypass boundaries;
- give every changefeed consumer its own tenant-safe contract because CDC is
  not automatically RLS-filtered;
- create vector indexes before bulk loading where the v26.2 backfill limitation
  applies, and revalidate distributed-scale optimizer and backfill behavior;
- treat TTL as asynchronous and preserve explicit CDC interaction controls;
- treat AS OF SYSTEM TIME as GC-window-bounded history, not durable audit
  storage;
- revalidate natural retry behavior and the combined TTL/changefeed case on a
  production-like cluster;
- keep local single-node evidence distinct from distributed, failure,
  performance, and operational guarantees.

## 11. Final Verdict

`MEMORY PATCH STEP 3 CLOSED — COCKROACHDB v26.2 BASELINE PINNED`

Step 4 was not begun.
