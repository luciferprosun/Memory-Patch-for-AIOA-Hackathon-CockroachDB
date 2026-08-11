# Step 35 Personal Memory UI Closure 1A

## Starting point and scope

- Exact Step 34 base: `9dce1e9192e98d38f3af64d736effa3b017788b8`.
- Step 34 was complete and pushed; Steps 35 and 36 were not started.
- The user explicitly approved FastAPI, Jinja2 and HTMX with OIDC
  Authorization Code + PKCE, opaque server-side sessions, secure cookies,
  CSRF protection and server-derived tenant/owner identity.
- Scope is limited to the owner-facing Personal Memory interface over the
  existing Step 27-33 services. Step 36 credential architecture hardening
  remains not started. This record does not invent the final Step 35 Git
  closure SHA before that commit exists.

## UI and authentication boundary

Step 35 adds a server-rendered FastAPI/Jinja2 workspace with a locally vendored
and SHA-256-checked HTMX 2.0.8 asset. Dependencies are exactly pinned and the
asset check is the repository-native UI build gate. Jinja autoescaping,
Content Security Policy, HSTS, no-store responses, bounded requests, inert
content rendering and safe error projections protect untrusted Personal
Memory and model text.

The OIDC client performs Authorization Code + PKCE and verifies issuer,
endpoint transport, signature, algorithm, audience, expiry, issued-at,
subject and nonce. The browser holds only an opaque Secure, HttpOnly,
SameSite=Lax session handle; the server derives tenant and owner from the
verified identity. Mutations are POST-only and require the exact session CSRF
token plus same-origin validation. No owner or tenant form value is trusted.

The package exposes an injected bounded session-store interface. Production
hosts must provide durable session storage and their own IdP configuration;
neither secrets nor credentials are committed or rendered.

## Owner workspace and business services

The dashboard and slot detail are bounded owner-scoped projections. They show
truthful empty state, slot state/configuration, quota usage, provider-neutral
model bindings, exact patch lifecycle, pending owner approval and bounded
Step 33 history. Personal Memory is explicitly labelled private and
non-canonical.

All writes reuse typed backend authority:

- Step 27 owns slot configuration/state and model bindings, including current
  configuration version and hard quota enforcement;
- Step 30 alone owns exact hash/version-bound human approval;
- Step 32 owns revocation, canonical deterministic owner export and logical
  deletion; and
- Step 33 supplies owner-visible audit references.

Receipts and reloaded backend state drive the UI. The browser cannot set a
patch ACTIVE, increment a version, decide quota, manufacture a receipt, call
the Commit Helper, publish a source or connect to CockroachDB. Archive,
revocation and deletion retain distinct wording and services. Deletion is
reported truthfully as logical, not physical.

## Validation

The canonical evidence file is
`docs/evidence/ui/step35-personal-memory-ui-validation.json`. Its controlled
run used real Step 27-32 fixtures and an owned disposable CockroachDB v26.2.4
runtime. All 17 migrations applied and replayed idempotently without a new
Step 35 migration.

The run proved dashboard/slot/lifecycle rendering, exact Step 30 approval and
stale denial, Step 27 model-binding mutation and quota denial, Step 32 export,
revocation and logical deletion, bounded Step 33 history, CSRF, stored-XSS
escaping, zero unsafe GET mutations and server-side authorization. User B was
denied direct access and approval, model-binding, export, revocation and
deletion mutations against User A; Tenant B was denied as well. RLS and FORCE
RLS were verified on the six private tables touched by the workspace.

The disposable database, application role, Commit Helper role, temporary
store, exact process and ports were removed without forced termination. The
canonical validation digest is
`a267300197aeb5f24363eea55cb90de033cfba6544706c83fae3767ffcbd4330`.

Focused UI/security tests, the vendored-asset gate, Python compilation,
offline migration validation, the contract validator and the full repository
regression are required closure gates. Post-commit focused, asset, contract
and controlled validation are run again before push.

## Known limitations and authority boundary

- The controlled run uses a deterministic synthetic OIDC owner and ASGI test
  client; it does not claim a production IdP or browser-automation result.
- A production host must inject a durable owner session store and approved
  OIDC configuration.
- Slot allocation remains a trusted host operation because selecting or
  creating a quota policy is not browser authority.
- Step 35 does not redesign credentials, roles or Commit Helper authority.
  Those are Step 36.
- The owner workspace contains no reviewer queue, source publication,
  canonical-evidence upgrade, external execution authority or shared-by-
  default behavior.

`Step 36: NOT STARTED`.
