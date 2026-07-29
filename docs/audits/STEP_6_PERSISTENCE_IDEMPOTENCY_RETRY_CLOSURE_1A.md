# Memory Patch — Step 6 Persistence, Idempotency, and Retry Closure 1A

## Status

`IMPLEMENTATION AND LIVE VALIDATION COMPLETE`

The final Git commit identity is recorded in the operator closure report rather
than self-referenced inside that commit.

## Step identity

- Step: `Step 6 — Persistence Adapters, Idempotency and Transaction Retry
  Foundation 1A`
- Required starting HEAD:
  `5bc6a11967d56bb1dc646d51de7c5560eabcb93b`
- Pinned runtime: CockroachDB `v26.2.4`
- Cluster version: `26.2`
- Branch: `main`

Step 7 was not started.

## Implementation

The persistence package supplies immutable typed values, sanitized error
types, narrow DB-API protocols, a ten-attempt `40001`-only serializable
transaction runner, an external-call transaction guard, compare-and-set
idempotency/resume operations, and representative immutable writes.

No pinned PostgreSQL driver was present, so no dependency was installed and no
production connection factory was invented. Deterministic unit tests use fake
connections; live SQL conformance uses the exact pinned CockroachDB full server
through the repository-owned CLI harness. Production driver and pool wiring
remain explicitly outside Foundation 1A.

Migration `0005_step6_persistence_idempotency_retry_foundation` adds only
`memory_patch.persistence_operations`. The table has:

- separate tenant-shared and user-private idempotency uniqueness;
- complete composite external-reference uniqueness;
- bounded digests, statuses, attempts, error metadata, and timestamps;
- compare-and-set mutable workflow state with immutable identity binding;
- SELECT/INSERT/UPDATE policies, RLS, and FORCE RLS;
- ownership by `mp_schema_owner` and only SELECT/INSERT/UPDATE runtime grants.

Migrations `0001` through `0004` retain their accepted hashes. Historical Step
5 evidence remains unchanged. The current chain has five migrations, 31
tables, 28 RLS+FORCE protected tables, and three identity guards.

## Retry and idempotency results

Deterministic tests proved:

- one normal serializable transaction commits once;
- callback-time and commit-time `40001` replay the complete callback;
- multiple `40001` signals can recover;
- attempt 10 raises a typed exhausted error;
- rollback, marker clearing, cursor close, and connection close happen before
  backoff;
- `23503`, `23505`, `23514`, `42501`, `22023`, `0A000`, and unknown SQLSTATE
  values are never automatically retried;
- driver error text is not exposed;
- process interruption is cleaned up and propagated;
- nested and stale transaction-handle use fails closed.

First idempotency claim creates one logical operation. Exact in-progress and
completed duplicates return prior state/result without silently repeating
external or domain work. Different request/scope bindings raise a typed
conflict. Interrupted state resumes only through compare-and-set with a
monotonic attempt count. Failed-final state does not silently resume.

The neutral external tuple includes tenant, origin kind/system/version,
adapter version, artifact kind, and external reference. Exact tuples
deduplicate; each changed dimension remains distinct; partial tuples fail.
The reference grants no access or authority.

## Immutable writes and authority

The representative adapter creates kernel-run identity, stores immutable
source snapshots and evidence items, and appends audit events. Exact replay
returns the same facts. Reusing an identity with a different digest, lineage,
or payload fails closed. The adapter does not approve, commit, activate,
publish, execute, or call a model or external system.

No model, provider, HAT, critic, external agent, NVIDIA component, routing
record, or evidence record receives a database role or approval, commit, or
execution authority. CockroachDB workflow rows remain facts below the
canonical authority boundary.

## Live validation

The bounded harness used one disposable, loopback-only, insecure,
single-node, in-memory CockroachDB server with external I/O disabled. It
verified:

- binary SHA-256
  `a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf`;
- build tag `v26.2.4`;
- build commit `80586181eb50e380e2cc982f61841eaf38af9982`;
- Linux amd64 platform;
- server version `v26.2.4` and cluster version `26.2`;
- five migrations from zero and five-migration checksum no-op;
- identical second-database schema digest
  `b6ef78272002f52de4d6e19d5b63344e6b79d05d6dfa8464e3763d1c697489d6`;
- identical Step 6 security digest
  `49b4bca5b10f6a0a5e612784900e56fdb2aa84cc8a352a6113ce328c5cb6f3a1`;
- three Step 6 policies, three runtime grants, one identity trigger, and three
  unique indexes;
- every required retry, idempotency, external-reference, immutable-write,
  transaction-boundary, RLS, authority, reproduction, and cleanup scenario.

Representative live SQLSTATE values were:

- uniqueness conflict: `23505`;
- invalid completion or partial external tuple: `23514`;
- RLS/privilege or immutable identity denial: `42501`.

Nonadministrative principals observed zero cross-tenant and same-tenant
cross-user private rows and could not insert or update outside exact context.
Tenant-only context observed no private operation. Runtime had no DELETE or
`BYPASSRLS`. The table owner observed zero rows without context under FORCE
RLS; root/admin remained an explicitly privileged operational boundary.

The sanitized tracked evidence is
[`step6-persistence-validation.json`](../evidence/cockroachdb-v26-2/step6-persistence-validation.json).

## Cleanup and limitations

Both fresh databases and all disposable/fixed roles were removed. The exact
owned server exited, loopback listeners closed, and the temporary store was
removed without force-kill. No runtime path or CockroachDB test process
remained.

Insecure loopback transport validates database and policy semantics, not
production certificates, authentication, SSO, secrets, pool behavior, or
distributed contention frequency. Production connection-factory wiring is
still external. Missing external result/receipt remains non-success.

The Step 3 natural client-visible `40001` and combined TTL/changefeed items
remain deferred. No Step 7 S3/Object Lock work, Step 36 credential hardening,
German-law persistence rule, benchmark dataset, NVIDIA integration, or
external-execution subsystem was added.

## Verdict

`MEMORY PATCH STEP 6 RETRY-SAFE PERSISTENCE FOUNDATION VERIFIED`
