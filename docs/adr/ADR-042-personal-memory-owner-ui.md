# ADR-042: Server-rendered owner Personal Memory workspace

## Status

Proposed. It becomes accepted only when the Step 35 closure commit is
reachable on `origin/main`.

## Context

Steps 27-33 provide owner-private slots, patch proposal/approval/activation,
retrieval, lifecycle controls and audit history. Step 34 intentionally adds a
separate reviewer service but no owner interface. Step 35 needs a small UI
that exposes existing owner operations without moving authorization or
state-machine logic into the browser.

The repository had no canonical browser framework. The user explicitly
approved FastAPI + Jinja2 + HTMX with OIDC Authorization Code + PKCE,
server-side sessions, secure cookies, CSRF and server-derived tenant/owner
identity for this step.

## Decision

1. Use pinned FastAPI/Jinja2 and a vendored, digest-checked HTMX asset. Keep
   the interface server-rendered and introduce no second SPA/build framework.
2. Authenticate owners with standards-based OIDC Authorization Code + PKCE.
   Verify token signature and claims, keep only opaque browser session
   handles, and derive tenant/owner identity exclusively on the server.
3. Treat the frontend as presentation, never authority. All reads are
   owner/tenant scoped under USER_PRIVATE RLS context; all writes call exact
   Step 27, Step 30 or Step 32 services.
4. Bind mutations to CSRF, current object hashes, expected state/configuration
   versions and independent idempotency identities. Render backend receipts
   and actual resulting states rather than optimistic authority changes.
5. Show quota and provider-neutral model bindings. Owner binding changes use
   the exact Step 27 typed service and hard quota. The browser cannot edit a
   quota policy.
6. Step 30 remains the only approval authority. Do not expose Commit Helper
   or activation endpoints to the browser.
7. Keep Personal Memory visibly private and non-canonical. Provide no source
   publication or shared-by-default action.
8. Separate archive, revocation and logical deletion. Export and deletion use
   the Step 32 service, not client-side reconstruction or generic mutation.
9. Apply Jinja autoescaping, POST-only mutations, CSRF and origin checks,
   bounded inputs, CSP/HSTS/security headers, safe errors and locally pinned
   assets. Do not send raw Personal Memory to third-party analytics.
10. Keep Step 34 reviewer routes and roles outside the owner package. Step 36
    remains the owner of deeper credential and Commit Helper hardening.

## Consequences

The UI is small, dependency-pinned and testable without a provider, browser
database access or a frontend credential. It can be mounted in an existing
ASGI host with an injected OIDC client, durable session store and Personal
Memory backend. The host must provide its production identity-provider
configuration and session persistence.

The server-rendered first release favors clear receipt-driven workflows over
rich client-side state. Slot allocation stays with the host because choosing
or creating a quota policy is not browser authority.

## Rejected alternatives

### Add a standalone SPA framework

Rejected because no canonical SPA stack existed and it would add a second
application architecture, a larger dependency surface and duplicated state.

### Trust tenant or owner fields from the browser

Rejected because hidden inputs and URL parameters are not authentication.

### Expose Commit Helper after approval

Rejected because the browser is not the technical commit authority and Step
36 owns further hardening of that boundary.

### Build a client-side export from visible cards

Rejected because it would omit lifecycle history and bypass the exact Step 32
export contract.

`Step 36: NOT STARTED`.
