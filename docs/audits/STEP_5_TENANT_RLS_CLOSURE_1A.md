# Memory Patch — Step 5 Tenant RLS Closure Record 1A

## Status

`IMPLEMENTATION AND LIVE VALIDATION COMPLETE`

The final Git commit identity is recorded in the operator closure report rather
than self-referenced inside that commit.

## Step identity

- Step: `Step 5 — Tenant Roles, Session Context and Row-Level Security 1A`
- Required starting HEAD:
  `ba825353d1a3df2e455f60061477cfa87cab08f9`
- Pinned runtime: CockroachDB `v26.2.4`
- Cluster version: `26.2`
- Branch: `main`

Step 6 was not started.

## Implementation

Migration `0004_step5_tenant_roles_session_context_rls` adds four fixed
least-privilege `NOLOGIN` roles, a trusted transaction-bound request-context
boundary, exact grants/revokes, two identity guards, 50 command-specific
policies, RLS, and FORCE RLS.

All 29 Step 4 tables are classified in
`config/cockroachdb/rls-security-1a.json`. Twenty-seven tenant-scoped tables
are protected. `hat_manifests` is a global, authority-inert read-only
definition table. `schema_migrations` is inaccessible migration bookkeeping.
The additional `request_contexts` table is security-internal and has no
ordinary runtime table grant.

The three Step 4 migration SHA-256 values remain unchanged. Cluster role
creation is idempotent and explicitly non-atomic with database-local DDL.
Nine idempotent database phases prevent a false all-or-nothing claim and
respect the verified CockroachDB schema-change limit. Migration `0004` is
recorded only after exact security catalog checks pass.

## Isolation and authority results

The full live harness passed 95/95 probes:

- 1 migration/reproduction;
- 1 all-table coverage;
- 12 positive paths;
- 16 cross-tenant denials;
- 17 cross-user Personal Memory denials;
- 20 session-context denials/reset checks;
- 7 FORCE RLS and owner-bypass checks;
- 21 role and authority escalation checks.

The catalog contained 27 RLS+FORCE tables, 50 policies, 51 runtime table
grants, 2 identity guards, and no runtime-owned protected table. All fixed and
test runtime roles had `rolbypassrls = false`, `rolcreaterole = false`,
`rolcreatedb = false`, and `rolsuper = false`.

Representative observed SQLSTATE values:

- RLS/privilege/identity denial: `42501`;
- malformed or incomplete context: `22023`;
- unknown or cross-tenant parent identity: `23503`;
- unsupported session authorization: `0A000`;
- CockroachDB no-op revoke warning: `01006`, followed by catalog proof that
  the runtime grant remained intact.

No cross-tenant or same-tenant cross-user private row was exposed or modified.
Tenant-shared Knowledge HAT access remained tenant-local and functional.
Tenant-only context exposed zero Personal Memory rows. An untrusted
caller-defined session variable exposed zero rows.

No model, provider, HAT, critic, routing record, evidence record, or external
agent received a database role or approval/commit/execution authority. The two
triggers only prevent identity/evidence rebinding and cannot perform a
lifecycle transition.

## Runtime evidence

The official full server identity was:

- binary SHA-256:
  `a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf`;
- build tag: `v26.2.4`;
- build commit: `80586181eb50e380e2cc982f61841eaf38af9982`;
- platform: Linux amd64;
- cluster version: `26.2`.

The harness applied all four migrations to a fresh database, replayed them as
a four-migration checksum no-op, applied them to an independent second
database, and reproduced security digest
`60f8814bf5a1eb9b210521e4deed6959f927c6941e155c357bd81cb7127d09ef`.

The sanitized tracked evidence is
[`step5-rls-validation.json`](../evidence/cockroachdb-v26-2/step5-rls-validation.json).

## Cleanup and local-runtime limitation

The harness used one disposable, loopback-only, insecure single-node runtime
with bounded memory and an in-memory store. Root/admin performed bootstrap,
catalog inspection, fixtures, and cleanup only; tenant/user assertions used
nonadministrative SQL logins.

Both databases and all fixed/test roles were removed. The exact owned server
exited gracefully, all ports closed, and the owned temporary store was
removed. No force-kill was used.

Insecure loopback transport validates SQL isolation semantics. It does not
validate production certificates, transport authentication, SSO, credential
storage, or Step 36 separation.

## Scope retained

- Application authentication is not implemented.
- Step 6 persistence adapters, idempotency, and transaction retry are not
  implemented.
- The Step 3 natural client-visible `40001` and combined TTL/changefeed
  deferrals remain open.
- RLS is not claimed to secure root/admin, migrations, backup/restore,
  replication, constraints, `TRUNCATE`, or changefeeds.
- No German-law-specific rule or corpus data was added.
- No NVIDIA, NOOA, OpenShell, CandidateActionEnvelope, policy, role,
  dependency, or runtime was added.
- Step 36 remains the dedicated credential-hardening step.

## Verdict

`MEMORY PATCH STEP 5 SQL ISOLATION BOUNDARY VERIFIED`
