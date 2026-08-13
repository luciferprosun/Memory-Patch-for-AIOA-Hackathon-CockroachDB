# Post-Roadmap Demo Runtime R2-R7 1A

## Status

R2 establishes the canonical ASGI composition boundary and controlled
launcher. R3 binds that boundary to the canonical CockroachDB migration
runner and a purpose-specific application pool. R4 binds the existing OIDC +
PKCE flow to deny-by-default judge access and durable bounded CockroachDB
sessions. R5 binds the existing pinned OpenRouter adapter behind durable
request/call accounting, a conservative paid-call ceiling and bounded
concurrency. R6 closes liveness/readiness and proves the real German Law path
through the assembled runtime. R7 performs the final regression, recovery and
repository closure gate. Deployment remains a separate post-R7 operator
activity; this runbook does not deploy or expose a public endpoint.

## Canonical runtime surface

ASGI target:

```text
aioa_memory_kernel.demo_runtime.asgi:app
```

Controlled launcher:

```bash
.venv/bin/python scripts/run_demo_runtime_1a.py check-config
.venv/bin/python scripts/run_demo_runtime_1a.py prepare-database
.venv/bin/python scripts/run_demo_runtime_1a.py serve
```

The launcher defaults to `127.0.0.1:8000`. `LOCAL_DEMO` rejects a non-loopback
bind. `prepare-database` applies and replays the fixed migration manifest,
closes migration authority, then validates the bounded normal application
pool. `serve` runs that same preflight before starting Uvicorn and assembles
the existing owner services and pinned provider behind the R5 guard. There is
no fallback to an in-memory session, database, provider or model.

## Non-secret configuration names

Required for `LOCAL_DEMO` and `HOSTED_DEMO`:

- `AIOA_RUNTIME_MODE` with `LOCAL_DEMO` or `HOSTED_DEMO`
- `AIOA_OIDC_ISSUER`
- `AIOA_OIDC_CLIENT_ID`
- `AIOA_RUNTIME_PUBLIC_ORIGIN`
- `AIOA_JUDGE_ALLOWED_OIDC_SUBJECTS`
- `DATABASE_URL_APP`
- `DATABASE_URL_MIGRATOR`
- `OPENROUTER_API_KEY`
- `AIOA_DEMO_PROVIDER_BUDGET_EPOCH`
- `AIOA_DEMO_PROVIDER_TENANT_ID`

Optional:

- `AIOA_RUNTIME_BIND_HOST`, default `127.0.0.1`
- `AIOA_RUNTIME_PORT`, default `8000`
- `AIOA_DB_POOL_MIN`, fixed to the Step40 value `1`
- `AIOA_DB_POOL_MAX`, fixed to the Step40 value `4`
- `AIOA_DB_ACQUISITION_TIMEOUT_SECONDS`, bounded `1..15`, default `5`
- `AIOA_DB_CONNECTION_TIMEOUT_SECONDS`, bounded `1..15`, default `5`
- `AIOA_DB_STATEMENT_TIMEOUT_SECONDS`, bounded `1..60`, default `10`
- `AIOA_DB_MIGRATION_TIMEOUT_SECONDS`, bounded `1..300`, default `300`
- `AIOA_DEMO_MAX_REQUESTS_TOTAL=24`
- `AIOA_DEMO_MAX_REQUESTS_PER_OWNER=8`
- `AIOA_DEMO_MAX_REQUESTS_PER_SESSION=6`
- `AIOA_DEMO_REQUEST_WINDOW_SECONDS=60`
- `AIOA_DEMO_MAX_REQUESTS_PER_WINDOW_GLOBAL=12`
- `AIOA_DEMO_MAX_REQUESTS_PER_WINDOW_OWNER=4`
- `AIOA_DEMO_MAX_REQUESTS_PER_WINDOW_SESSION=3`
- `AIOA_DEMO_PROVIDER_MAX_CALLS_TOTAL=32`
- `AIOA_DEMO_PROVIDER_MAX_CALLS_PER_OWNER=12`
- `AIOA_DEMO_PROVIDER_MAX_CALLS_PER_SESSION=10`
- `AIOA_DEMO_PROVIDER_MAX_CALLS_PER_REQUEST=8`
- `AIOA_DEMO_PROVIDER_MAX_CONCURRENT_CALLS=1`
- `AIOA_DEMO_PROVIDER_MAX_QUEUED_CALLS=2`
- `AIOA_DEMO_PROVIDER_QUEUE_WAIT_SECONDS=2`
- `AIOA_DEMO_PROVIDER_MAX_INPUT_BYTES=24576`
- `AIOA_DEMO_PROVIDER_MAX_OUTPUT_TOKENS=1024` (the frozen Draft V2 contract maximum)
- `AIOA_DEMO_PROVIDER_TIMEOUT_SECONDS=45`

`DATABASE_URL_APP` and `DATABASE_URL_MIGRATOR` must address the same database
with different credentials and different principals. `HOSTED_DEMO` accepts
only `sslmode=verify-full`; there is no TLS downgrade. An explicitly selected
`TEST` or `LOCAL_DEMO` process may use `sslmode=disable` only for a loopback
disposable database and only with `AIOA_DB_ALLOW_INSECURE_LOCAL=1`.

Do not place credentials in command-line arguments. Database credentials are
loaded through the Step36 purpose boundary and are never printed. The
migration credential is short-lived, is closed before the normal application
pool opens, and is never retained as a request-time fallback.

`OPENROUTER_API_KEY` is also loaded only through the Step36 server-side
provider purpose. Its value is excluded from configuration representations,
logs, sessions, templates, HTMX responses and browser assets. The budget epoch
is a non-secret operator-selected identifier, not a key or monetary amount.

## CockroachDB and migration contract

- CockroachDB is pinned to `v26.2.4`.
- The existing `scripts/run_cockroachdb_migrations.py` runner and its 19-file
  manifest remain the only migration system.
- The latest migration is
  `0019_post_roadmap_demo_runtime_state`.
- Unknown migration IDs, checksum mismatch, a ledger gap, failed application,
  failed replay or the wrong server version fail startup closed.
- The fixed SQL reuses the already validated Step18/30/35 statement-wise
  autocommit transport: its quote-aware splitter preserves function bodies and
  omits only the outer `BEGIN`/`COMMIT` wrapper tokens. A ledger row is written
  only after every statement and catalog assertion succeeds, so partial DDL
  never permits the application pool to open and cannot be reported as an
  applied migration.
- Every DDL statement has the configured hard timeout, safe cancellation and
  owned-socket fail-closed fallback. The complete migrator session has an
  additional fixed 45-minute ceiling.
- After migration replay, the long-lived principal must be a member of
  `mp_app_runtime` and `mp_request_context_setter`, must have no inherited
  `BYPASSRLS` or administrative authority, and must not hold schema or
  migration-ledger privileges.
- The authority probe requires the complete post-R4 58-table schema and
  all 52 protected tables to retain both RLS and FORCE RLS. The earlier
  `43/40` offline migration summary intentionally covers the Step 4-27 schema
  subset and is not used as the hosted-runtime admission threshold.

The application pool follows the Step40 profile exactly: minimum `1`, maximum
`4`, one pool worker, bounded acquisition, and no unbounded reconnect or
connection wait. Pool leases use transport-level autocommit solely so the
repository can establish its own explicit serializable transaction; business
operations do not run outside that transaction boundary. Persistence retries
remain the existing transaction-layer policy for CockroachDB serialization
SQLSTATE `40001` only; authentication, TLS, migration, authorization and
arbitrary SQL failures are never retried as if they were serialization
conflicts.

## Lifecycle

R2 fixes this startup sequence:

1. typed runtime and purpose-specific database configuration validated;
2. hosted TLS and principal separation validated;
3. migration connection opened, state inspected, pending canonical migrations
   applied, replayed and re-inspected;
4. migration authority closed;
5. bounded application pool opened and its version/schema/RLS/role authority
   validated;
6. session resources initialized;
7. service composition initialized;
8. provider adapter initialized without a completion call;
9. runtime guards initialized;
10. application started.

Shutdown rejects newly owned work first, then closes explicitly owned
background, provider, session and database resources in reverse order.
Unowned resources, corpus data, external model/cache data and user files are
never removed.

Use `Ctrl-C` for an attached local process or send `SIGTERM` to the exact
owned Uvicorn process. Uvicorn then exits through the application lifespan:
readiness becomes false before provider, session and database resources are
closed. Do not use broad process-kill commands.

If migration or pool initialization fails, startup remains unavailable and
only already-owned resources are closed. No app pool is created against an
unknown schema, and no admin/master credential fallback is attempted.

## Health and traffic admission

`GET /health/live` returns `200` with only `{"status":"LIVE"}` when the ASGI
process can answer. It performs no database, retrieval, OIDC or provider work.

`GET /health/ready` returns `200` only after the complete hosted dependency
set is safe. Before startup, on a detected dependency failure, and throughout
shutdown it returns `503` with a bounded sanitized reason code. Readiness
requires:

- validated runtime, judge-auth and pinned provider configuration;
- verified CockroachDB TLS policy, completed migration state and the bounded
  normal application pool;
- the durable CockroachDB session store;
- the durable provider call/spend guard;
- mandatory services and the exact deterministic startup sequence.

Neither health endpoint performs a paid provider completion or exposes a DSN,
credential, owner/session value or internal traceback.

## Safe local disposable validation

Use only an owned loopback CockroachDB `v26.2.4` instance and synthetic
purpose-specific credentials. For the operator launcher select `LOCAL_DEMO`, set
`AIOA_DB_ALLOW_INSECURE_LOCAL=1`, and use loopback URLs with
`sslmode=disable`. The internal focused test harness may select `TEST`; the
launcher deliberately refuses to serve or prepare a database in `TEST` mode.
Never reuse either setting for `HOSTED_DEMO`. The launcher prints only
migration counts and sanitized status codes, never DSNs.

## R4 judge authentication and durable sessions

R4 reuses the Step35 OIDC Authorization Code + PKCE flow. Hosted access is
deny-by-default and requires an exact verified OIDC subject allowlist in
`AIOA_JUDGE_ALLOWED_OIDC_SUBJECTS` (comma-separated, at most 32 entries).
The browser cannot supply an authoritative owner, tenant, role, or judge flag.

Hosted mode uses `memory_patch.owner_ui_sessions` through the normal
`mp_app_runtime` pool. Only SHA-256 digests of opaque handles and the minimum
OIDC/session continuity fields are persisted. ID, access and refresh tokens
and every privileged credential are excluded. Migration
`0019_post_roadmap_demo_runtime_state` is the narrow schema change explicitly
authorized by the R1 handoff; it preserves RLS/FORCE RLS and adds no role.

Optional bounded server settings (defaults shown) are:

- `AIOA_SESSION_TTL_SECONDS=28800`
- `AIOA_OIDC_PENDING_TTL_SECONDS=600`
- `AIOA_SESSION_MAXIMUM_TOTAL=64`
- `AIOA_OIDC_MAXIMUM_PENDING_FLOWS=16`
- `AIOA_SESSION_MAXIMUM_PER_OWNER=4`
- `AIOA_SESSION_MAXIMUM_PAYLOAD_BYTES=2048`

Cookies remain Secure, HttpOnly, SameSite=Lax and opaque. State-changing
owner routes continue to require the existing CSRF and trusted server-derived
owner binding. Hosted mode has no in-memory store, test-login, insecure-cookie,
Redis or migration/admin fallback.

## R5 provider and budget guard

The provider identity remains frozen to `openrouter` and
`moonshotai/kimi-k2` through adapter
`openrouter-chat-completions-step38-1a`, endpoint class
`openrouter-public-chat-completions-v1`, origin `https://openrouter.ai` and
path `/api/v1/chat/completions`. Tools, web browsing, function calling and code
execution remain disabled. There is no provider or model fallback.

Every paid call requires a trusted server-derived tenant, owner, opaque
session and request scope. Before transport, the runtime:

1. admits the request through bounded global, owner and session windows;
2. waits at most two seconds for the single provider permit, with at most two
   queued callers;
3. reserves global and owner call records in
   `memory_patch.persistence_operations` under the normal application role;
4. rejects exhausted global, owner, session or per-request ceilings without
   calling the provider;
5. invokes only the pinned adapter with bounded input, output and timeout;
6. reconciles the reservation with a safe result digest and bounded,
   explicitly provider-reported token counts.

The provider response contract supplies token counts but not a verified billed
price. R5 therefore uses a **CALL-COUNT CEILING**, not a claim of exact cost.
Unknown completion remains charged conservatively and is never refunded. The
named budget epoch and its reservations survive a single-process restart in
CockroachDB. Re-arming the budget requires an explicit operator change to
`AIOA_DEMO_PROVIDER_BUDGET_EPOCH`; restarting the process alone cannot reset
the hard total. `AIOA_DEMO_PROVIDER_TENANT_ID` pins the hosted guard to the one
authorized demo tenant. Requests carrying any other server-derived tenant are
rejected before accounting or transport, so the tenant-scoped RLS ledger's
global count is also the true global count for this runtime.

No raw prompt, response, Personal Memory content or provider credential is
stored in the accounting ledger. The ledger is observational and has no
approval, Commit Helper, review, source-publication or Personal Memory
activation authority.

### Golden Path paid-call envelope

| Flow | Minimum paid calls | Maximum bounded paid calls | Basis |
| --- | ---: | ---: | --- |
| Primary case | 2 | 4 | Draft V1 and Draft V2; each canonical adapter policy permits at most two same-provider attempts. |
| Backup branch | 3 | 6 | Observe primary Draft V1 once, then run backup Draft V1 and Draft V2 under the same bounded policies. |
| Personal Memory later retrieval/reuse | 0 | 0 | Existing retrieval is provider-neutral and performs no hosted inference. |
| Optional Critic | 1 | 2 | One candidate-only call with the canonical bounded attempt policy; disabled by default in the 4 GB profile. |

The maximum backup-plus-Critic branch is eight calls, equal to the per-request
ceiling and still bounded by the tighter owner, session and global totals.
Retries count as paid attempts. Local retrieval and deterministic verification
do not consume provider-call budget. The total ceiling of 32 permits at most
four worst-case eight-call rehearsals across the armed epoch; the owner ceiling
of 12 and session ceiling of 10 impose tighter limits on any one judge. These
are operator-selected demo bounds, not an assertion about the account balance.

## Deployment boundary and known limitations

The controlled R6 proof uses the canonical one-worker
`memory-patch-4gb-demo-1a` profile, CockroachDB `v26.2.4`, the durable session
store and the guarded `openrouter` / `moonshotai/kimi-k2` adapter. It is a
loopback hosted-style proof, not an AWS or public-Internet deployment. The
external OIDC provider and final HTTPS redirect URI remain deployment-time
prerequisites. Multi-worker distributed rate-window coordination is outside
this single-process demo contract, while the CockroachDB-backed hard call
ceiling remains restart-safe.

Do not deploy when any of these conditions is true:

- `check-config` or `prepare-database` fails;
- `/health/ready` is not `200`;
- HTTPS termination, verified CockroachDB TLS or the exact OIDC callback is
  unavailable;
- the judge allowlist is absent or test authentication is enabled;
- sessions are not CockroachDB-backed or Secure cookies cannot be preserved;
- the provider/model differs from the pinned identity, the provider budget is
  not explicitly armed, or any privileged secret could reach browser code;
- the one-worker 4 GB profile and its bounded pool/concurrency settings cannot
  be honored.

R2-R7 introduces no provider fallback, new authority or Step44. Startup and
health checks make no paid model call. The single bounded live call sequence
used for R6 proof is referenced by the final R7 closure evidence rather than
repeated for presentation.
