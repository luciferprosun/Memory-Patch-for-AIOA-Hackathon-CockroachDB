# ADR-012: CockroachDB Tenant Roles, Transaction Context, and Forced RLS

- Status: Accepted
- Date: 2026-07-28

## Context

Step 4 created 29 tenant-ready tables with exact tenant, user, HAT-scope, and
lineage keys, but intentionally created no SQL roles or RLS. Application
filters alone cannot enforce tenant or Personal Memory isolation, and a
runtime table owner or a role with `BYPASSRLS` would invalidate an RLS design.

CockroachDB `v26.2.4` supports command-specific RLS and FORCE RLS. It accepts
caller-defined session variables, but an untrusted caller that can choose any
tenant value is not an identity boundary. It also does not support all
PostgreSQL `SECURITY DEFINER` configuration syntax.

## Decision

Create four fixed cluster-scoped `NOLOGIN`, `NOCREATEROLE`, `NOCREATEDB`,
`NOBYPASSRLS` roles:

- `mp_schema_owner`;
- `mp_security_owner`;
- `mp_app_runtime`;
- `mp_request_context_setter`.

Keep all fixed-role membership graphs empty. A separately authenticated future
application login may receive runtime and setter membership; no human user,
model, provider, HAT, critic, or external agent is represented by a permanent
product SQL login in Step 5.

Store request context under `(session_user, pg_backend_pid())` and bind it to
the current `transaction_timestamp()`. Require exact `TENANT_SHARED` or
`USER_PRIVATE` mode and validate tenant/user identity through Step 4 foreign
keys. Expose context mutation only through fully-qualified
`SECURITY DEFINER` functions owned by `mp_security_owner`. Grant policy
predicate execution separately to `mp_app_runtime`.

Enable and force RLS on all 27 tenant-scoped Step 4 tables. Use command-specific
policies with `USING` and/or `WITH CHECK`. Preserve only two explicit
exceptions: globally readable, runtime-immutable `hat_manifests`, and
runtime-inaccessible `schema_migrations`. Keep the new `request_contexts`
security table runtime-inaccessible and outside RLS because only its owner
functions access it.

Use two narrow `SECURITY INVOKER` update triggers to make stable Personal
Memory and memory-item identity/evidence columns immutable. They are data
identity guards, not lifecycle or authority automation.

Run Step 5 database DDL in nine explicit idempotent phases after idempotent
cluster-role DDL. Record migration `0004` only after live catalog validation
confirms exact role options, owners, memberships, RLS/FORCE flags, policies,
grants, and triggers.

## Alternatives rejected

- Untrusted custom session variables were rejected as the security boundary;
  callers can set them freely and every policy ignores them.
- One permanent SQL login per product user was rejected because no repository
  ADR requires it and it would confuse database identity with product
  authentication.
- Runtime ownership was rejected because it creates an avoidable bypass and
  DDL surface even when FORCE RLS is present.
- An allow-all tenant policy plus application filtering was rejected because
  it is not SQL-enforced isolation.
- A nullable owner fallback was rejected because tenant-only context must not
  reveal Personal Memory.
- A single giant Step 5 transaction was rejected after verified CockroachDB
  schema-change limits; cluster roles are also honestly non-atomic with
  database-local DDL.
- Dynamic SQL and unqualified security-definer references were rejected.
- Automatic approval, commit, activation, or publication triggers were
  rejected as authority escalation.

## Consequences

Positive:

- missing, malformed, stale, tenant-only, and spoofed context fails closed;
- Tenant A cannot read or mutate Tenant B rows;
- User A cannot read or mutate User B Personal Memory within one tenant;
- runtime has no ownership, BYPASSRLS, privileged inheritance, DDL, or grant
  authority;
- valid tenant-shared and user-private paths remain usable;
- policy and privilege coverage is machine-checkable for every Step 4 table;
- root/admin is explicitly outside the normal application path.

Constraints:

- application authentication remains mandatory and is not implemented here;
- pooled connections must begin a transaction and explicitly establish,
  replace, or clear context for every request;
- role DDL may survive a later failed database migration and must be inspected
  during diagnosis;
- backup/restore, replication, constraints, `TRUNCATE`, and changefeeds need
  controls outside RLS;
- insecure loopback tests do not prove production transport or credentials;
- Step 6 persistence and retry behavior and Step 36 credential hardening
  remain separate roadmap work.

The complete matrix, role graph, trust boundary, commands, and live results are
in the
[Step 5 architecture record](../architecture/COCKROACHDB_TENANT_ROLES_SESSION_CONTEXT_AND_RLS_1A.md).
