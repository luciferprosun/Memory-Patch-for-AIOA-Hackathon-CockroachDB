# Memory Patch — Step 4 Schema and Migration Closure Record 1A

## Status

`IMPLEMENTATION AND LIVE VALIDATION COMPLETE`

The final Git commit identity is intentionally recorded in the operator’s
closure report rather than self-referenced inside that commit.

## Step identity

- Step: `Step 4 — CockroachDB Logical Schema and Migration Foundation 1A`
- Required starting HEAD:
  `3e8c499fbcb2bb905fce451a163f913030ecacce`
- Pinned runtime: CockroachDB `v26.2.4`
- Cluster version: `26.2`
- Branch: `main`

Step 5 was not begun.

## Canonical contract reconciliation

The implementation was derived from the repository’s Step 1 contracts and
state machines, the Step 2 authority closure, and the Step 3 capability
baseline. Roadmap nouns were mapped to contract concepts rather than creating
duplicate product models:

- shared Knowledge HATs use non-authoritative manifests plus explicit shared
  HAT scopes;
- Personal Memory HATs use exact tenant/user/personal-space ownership plus
  explicit private HAT scopes;
- source lineage uses source → snapshot → version → chunk;
- routing, action-policy, evidence, patch, approval, commit, and audit rows are
  persisted facts, not executable authority.

No unresolved contract conflict remained.

## Implemented foundation

Three immutable, forward-only migrations create 29 tables:

1. `0001_step4_identity_and_hat_scopes`;
2. `0002_step4_knowledge_lineage_and_retrieval`;
3. `0003_step4_kernel_memory_and_audit_evidence`.

The runner validates filename/ID order, SHA-256, exact target version,
forbidden Step 5 SQL, authority defaults, triggers, secrets, machine paths, and
domain-specific Kernel rules. It records a migration only inside the same
explicit transaction as the migration SQL. Reapplication validates checksums
and performs no duplicate work.

The machine-readable schema manifest records 29 tables and 9 explicit indexes.
The architecture record describes every table and includes the actual logical
relationship diagram.

## Live CockroachDB evidence

The bounded live run used the official Linux amd64 CockroachDB v26.2.4 binary:

- binary SHA-256:
  `a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf`;
- one disposable local single-node runtime;
- listeners bound only to `127.0.0.1`;
- bounded `640MiB` in-memory store;
- two unique `mp_step4_` databases.

Observed results:

- all 3 migrations applied from zero;
- the second invocation applied 0 and skipped all 3 by exact checksum;
- all 3 migrations applied to a second fresh database;
- both catalogs produced schema digest
  `e600f0bed5d77618f02df822f9164282e82950ae9f867f11718b68a11d050d23`;
- catalog: 29 tables, 289 columns, 44 foreign keys, 29 primary keys,
  24 unique constraints, and 9 explicit indexes;
- valid source → snapshot → version → chunk lineage persisted;
- Kernel run, routing decision, action-policy decision, structural approval,
  and separate commit receipt persisted;
- full-text storage returned the expected synthetic match.

Negative probes passed with deterministic CockroachDB SQLSTATE values:

- cross-HAT snapshot lineage: `23503`;
- cross-HAT chunk lineage: `23503`;
- cross-tenant version lineage: `23503`;
- cross-tenant chunk lineage: `23503`;
- cross-tenant snapshot lineage: `23503`;
- cross-owner Personal Memory HAT scope: `23503`;
- cross-owner commitment binding: `23503`;
- Personal Memory HAT scope substituted for a Knowledge HAT: `23503`;
- private scope without an owner: `23514`;
- shared scope carrying private ownership: `23514`;
- personal approval without owner/space: `23514`;
- personal approval carrying the wrong target scope: `23503`;
- personal commit without owner/space: `23514`;
- duplicate source identity: `23505`;
- model-originated approval claim: `23514`;
- HAT authority declaration other than `NONE`: `23514`;
- active verified memory without future materialization: `23514`;
- forbidden `PROPOSED → APPROVED` transition record: `23514`.

The sanitized result is
[`step4-schema-validation.json`](../evidence/cockroachdb-v26-2/step4-schema-validation.json).

## Cleanup

Both disposable databases were dropped and verified absent. Graceful SIGTERM
did not stop the exact owned CockroachDB PID within the bounded window, so the
harness used its final kill fallback on that PID only. The PID then exited, all
three loopback ports closed, and the exact temporary store was removed. No
broad process-kill command was used. No credentials, certificates, raw logs,
database stores, or runtime ports were committed.

## Authority and isolation boundary

Static checks prove:

- model and HAT actor types cannot be stored as approval claims;
- HAT authority declarations remain `NONE`;
- only the exact shared or exact personal HAT scope shape is representable;
- private Personal Memory HAT identity includes one tenant, one user, and one
  personal space;
- shared scope cannot carry personal ownership;
- proposal, approval, and commitment are separate exact bindings whose target
  scope and required private owner/space cannot be omitted;
- commit rows require an `APPROVE` approval reference;
- verified memory remains inactive at this layer;
- routing decisions contain no approval, commit, write, or authority field;
- triggers, automatic approval, automatic commitment, RLS, roles, and
  BYPASSRLS are absent.

The schema does not authenticate a claimed human, consume an approval, enforce
SQL tenant access, or execute a state transition. Those are future
authentication, Step 5, and Step 6 responsibilities.

## Retrieval decision

Full-text storage uses explicitly configured `simple`, `english`, or `german`
TSVECTOR rows and an inverted index. Tenant/HAT prefix indexes are query
filters, not authorization.

VECTOR DDL is deferred because no canonical embedding model or dimension is
pinned. Step 3’s `VECTOR(3)` was a synthetic capability probe and was not
promoted into the application schema.

## Validation evidence

Executed results:

- baseline `python3 scripts/validate_contracts.py` — PASS;
- baseline `python3 -m unittest discover -v` — PASS, 271 tests;
- `python3 scripts/run_cockroachdb_migrations.py --offline-validate` — PASS,
  3 migrations, 29 tables, 9 explicit indexes;
- `python3 scripts/run_cockroachdb_migrations.py --live-test --allow-live
  --cockroach-binary <verified-v26.2.4-binary> --json-output
  <step-owned-result>` — PASS in 438.788 seconds, including reproduction and
  complete cleanup;
- `python3 -m unittest tests.test_cockroachdb_schema_migrations -v` — PASS,
  57 tests;
- `env PYTHONPYCACHEPREFIX=<step-owned-cache> python3 -m compileall -q .` —
  PASS;
- final `python3 scripts/validate_contracts.py` — PASS;
- final `python3 -m unittest discover -v` — PASS, 328 tests, 0 failures,
  0 errors.

The first post-implementation full-suite run correctly exposed one stale Step
3 roadmap assertion that required Step 4 to remain open forever. That
transient assertion was replaced by a durable check of the Step 3 closure
evidence; the Step 4 test module independently enforces that Step 4 is complete
and Step 5 remains open.

Staging review, commit, push, and remote equality are recorded by the
operator’s final closure report after execution.

## Deferred and excluded

- Step 5 roles, session context, RLS, FORCE RLS, and BYPASS controls were not
  implemented.
- Step 6 persistence adapters, idempotency, and transaction retry were not
  implemented.
- Natural client-visible `40001` remains deferred from Step 3.
- Combined TTL/changefeed behavior remains deferred from Step 3.
- Vector dimension/model selection is a legitimate new DDL defer because no
  canonical dimension exists.
- No ingestion, S3 adapter, embedding, provider, model execution, corpus data,
  API, UI, or deployment was implemented.

## Verdict

`MEMORY PATCH STEP 4 TECHNICAL FOUNDATION VERIFIED`
