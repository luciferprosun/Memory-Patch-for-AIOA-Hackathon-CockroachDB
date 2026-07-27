# ADR-010: Pin the Validated CockroachDB v26.2 Runtime

- Status: Accepted
- Date: 2026-07-27

## Context

Memory Patch persistence needs a reproducible database baseline before its
logical schema and migrations are designed. A release-series name, `latest`
tag, documentation claim, or successful SQL parse is not sufficient evidence
that a capability has the required semantics.

Step 3 tested an exact disposable local runtime and recorded 34 `PASS`,
0 `FAIL`, and 2 `DEFER` decisions in the
[capability matrix](../evidence/cockroachdb-v26-2/capability-matrix.json).
The live plane was single-node and loopback-only, so it does not prove
distributed, production-load, or managed-service behavior.

## Decision

Pin the regular GA CockroachDB `v26.2.4` release for Step 4 and later Memory
Patch database work. The machine-readable source of truth is
[`config/cockroachdb/version-pin.json`](../../config/cockroachdb/version-pin.json).
The pin includes:

- exact version `v26.2.4`, target series `v26.2`, and server build tag
  `v26.2.4`;
- Linux `amd64` official archive source;
- vendor-verified archive SHA-256
  `3c7de055c07f9101eb0f71b3f5e6b489b0fcf449d3d5a55bfe61eff4f935ce8f`;
- extracted binary SHA-256
  `a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf`;
- live server identity, finalized cluster version `26.2`, and the official
  evidence used for selection.

At verification time, `v26.2.4` was the newest supported production patch in
the v26.2 series with a public, reproducible self-hosted Linux artifact and a
separately published vendor checksum. The numerically newer `v26.2.5` was not
selected because availability was limited to selected CockroachDB Cloud
clusters and support-coordinated self-hosted access; a public reproducible
binary and checksum were not available.

The official selection evidence is the
[v26.2 release record](https://www.cockroachlabs.com/docs/releases/v26.2),
[release support policy](https://www.cockroachlabs.com/docs/releases/release-support-policy),
[security advisories](https://www.cockroachlabs.com/docs/advisories), and the
[v26.2.4 vendor checksum](https://binaries.cockroachdb.com/cockroach-v26.2.4.linux-amd64.tgz.sha256sum).

## Capability Decisions

### Approved for later design

- `VECTOR(3)`-style fixed-dimension values and the observed Euclidean, cosine,
  and inner-product operators may be used with explicit dimension validation.
- English and German full-text vectors, queries, ranking, and supported
  inverted indexes may be used with fixed dictionary choices and regression
  fixtures.
- GA row-level security may enforce ordinary-role `SELECT`, `INSERT`, `UPDATE`,
  and `DELETE` isolation when tenant identity is supplied through a trusted
  session boundary.
- Partial unique indexes may enforce tenant-scoped current-row uniqueness.
  SQLSTATE `23505` is a permanent uniqueness result, not a retry signal.
- Row-Level TTL DDL and scheduled deletion may be used as asynchronous
  retention machinery; it is never a synchronous delete guarantee.
- The persistence baseline remains `SERIALIZABLE`. Only SQLSTATE `40001` is
  classified as retryable by this decision, with bounded attempts and backoff.

### Approved only with guards

- Vector indexes may be designed only with the pinned syntax, exact
  tenant/HAT prefix filters, an empty-table-first creation or separately
  reviewed backfill plan, and semantic fallback. Prefix columns constrain a
  query; they are not authorization.
- `FORCE ROW LEVEL SECURITY` is required where table-owner access must obey
  policy. Administrative, ownership, `TRUNCATE`, backup/restore, and
  replication boundaries require separate controls and tests.
- Changefeeds require an explicit tenant-safe consumer contract. CDC output
  must not be treated as RLS-filtered, and CDC queries on RLS tables are not an
  isolation mechanism.
- `AS OF SYSTEM TIME` may support bounded historical reads only within retained
  MVCC history. It is not durable audit storage.
- TTL deletion and changefeed use require independent lifecycle monitoring;
  metadata registration alone is not deletion proof.
- Synthetic SQLSTATE `40001` injection may test a retry classifier, but it must
  not be represented as natural contention evidence.
- Any capability whose official maturity remains unknown must retain a guard
  and a fallback even when its v26.2.4 semantic probe passed.

### Deferred

- A deterministic client-visible natural-contention `40001` remains deferred
  because ten bounded live attempts completed through transparent retry
  behavior without exposing that signal.
- The combined observation that a TTL-generated delete appears in a
  changefeed remains deferred; independent TTL deletion and sinkless
  changefeed semantics passed.
- External changefeed sinks, distributed contention, failover, load behavior,
  and production-like deployment semantics remain future-cluster
  revalidation work.

### Excluded

- `latest`, version ranges, prereleases, silent patch upgrades, and silent
  minor-series upgrades are prohibited.
- Preview or experimental features are excluded from the MVP unless a later
  ADR records an explicit guard, fallback, exact-runtime live evidence, and
  rollback decision.
- Unsupported `websearch_to_tsquery` behavior in v26.2.4 is excluded.
- Vector prefixes as authorization, RLS as automatic CDC filtering, AOST as
  permanent history, TTL registration as synchronous deletion, and a
  synthetic retry error as natural contention proof are prohibited
  assumptions.
- The Step 3 disposable probe objects are not a production schema and must not
  be promoted into migrations.

## Upgrade Policy

Changing the pin requires one reviewed change that:

1. names one exact GA patch and verifies its current support and advisory
   status from official Cockroach Labs sources;
2. obtains an official immutable artifact, verifies the vendor checksum when
   published, and records both archive and runtime binary digests;
3. proves exact client, server, build-tag, and cluster-version identity on a
   disposable live runtime;
4. reruns every mandatory capability and negative probe, records every
   `PASS`/`FAIL`/`DEFER`, and regenerates canonical evidence;
5. revalidates security boundaries, cleanup, offline tests, documentation, and
   repository regression tests;
6. documents compatibility, migration, fallback, and rollback consequences.

No operator, build script, container configuration, or deployment may replace
this pin with `latest` or upgrade a patch or minor series implicitly. Official
documentation or syntax acceptance alone cannot authorize a pin change.

## Consequences for Step 4

Step 4 must:

- fail closed when the client/server runtime is not exactly `v26.2.4`;
- design tenant ownership around RLS plus `FORCE ROW LEVEL SECURITY` where
  owner bypass is unacceptable, while keeping application roles non-admin;
- treat vector prefix columns as performance and query-scope dimensions only;
- keep CDC consumers independently tenant-safe and outside any assumption of
  RLS filtering;
- plan vector-index creation and backfill explicitly and preserve a non-ANN
  semantic fallback;
- pin full-text dictionaries and preserve English/German semantic tests;
- use partial uniqueness for applicable current-row invariants while keeping
  `23505` distinct from retryable `40001`;
- treat TTL as asynchronous, AOST as GC-window-bounded, and audit storage as a
  separate durable concern;
- implement bounded `SERIALIZABLE` retry handling in a later persistence
  layer, not reuse the Step 3 probe utility as a production adapter;
- revalidate distributed, managed-service, load, failover, CDC, and TTL
  interactions on a production-like cluster before production rollout.

The complete runtime evidence and limitations are maintained in the
[CockroachDB v26.2 capability baseline](../architecture/COCKROACHDB_V26_2_CAPABILITY_BASELINE_1A.md).
