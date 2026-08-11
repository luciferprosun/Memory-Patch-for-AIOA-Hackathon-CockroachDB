# Personal Memory UI 1A

## Scope and stack

Step 35 adds the owner-facing Personal Memory workspace over the existing
Step 27-33 contracts. The approved repository stack is server-rendered
FastAPI 0.141.1, Jinja2 3.1.6 and vendored HTMX 2.0.8. The browser receives
HTML and makes bounded semantic HTTP requests; it never receives a database,
Commit Helper, provider, AWS or repository credential.

The workspace is mounted under `/memory`. It provides a dashboard, empty
state, slot detail/configuration, exact slot transitions, quota, provider-
neutral model bindings, patch lifecycle, Step 30 owner approval, Step 32
revocation/export/logical deletion and bounded Step 33 owner-history
references. It does not contain the Step 34 reviewer workspace.

No Step 35 migration is required. Reads reuse the Step 27-33 tables and their
existing RLS/FORCE RLS policies; writes call the existing typed services.

## Authentication and owner scope

The production boundary is OIDC Authorization Code with PKCE. The client
validates discovery issuer, HTTPS endpoints, a closed RS256/ES256 algorithm
set, the exact JWK `kid`, signature, issuer, audience, expiry, issued-at,
subject and nonce. Tenant and owner IDs come only from verified token claims.
They are stored in a server-side session and never accepted from a form, URL
query or hidden field.

The browser stores only high-entropy opaque handles in Secure, HttpOnly,
SameSite=Lax cookies. The default memory session store is bounded and is for
local/controlled use; production injects a durable server-side implementation
through `OwnerSessionStore`. Session handles are stored only as SHA-256
digests. Login rotates the session and pending OIDC state is single-use.

Every database read runs as `USER_PRIVATE` with exact tenant and owner
context. Queries bind tenant and owner predicates before their fixed limits;
direct-object requests for another owner return no object. Existing database
RLS/FORCE RLS remains a second, independent boundary.

## Frontend and backend responsibility

The Jinja views are presentation projections: `DashboardView`, `SlotView`,
`PatchView`, `QuotaView`, `ModelBindingView` and `AuditEventView`. They are
immutable and contain neither mutators nor credentials. The browser cannot
set a lifecycle state, increment a version, create a receipt, decide quota or
change tenant/owner scope.

`KernelPersonalMemoryUiBackend` is a thin adapter. It:

- reads bounded owner slots, proposals/patches and audit references;
- configures or transitions a slot through `PersonalMemoryService`;
- adds/removes an exact Step 27 `EXACT_MODEL` binding through
  `ModelBindingCommand` and the existing binding quota;
- approves only through `PersonalMemoryApprovalService`;
- revokes, exports and logically deletes only through
  `PersonalMemoryLifecycle32Service`; and
- revalidates current hashes, state/configuration versions and exact object
  identity before constructing each command.

There is no public Commit Helper or activation route. Approval renders the
actual `APPROVED` result and explicitly states that commit and activation are
separate. The UI never optimistically claims `APPROVED`, `ACTIVE`, `REVOKED`
or `DELETED` before a backend receipt exists.

## Views and lifecycle semantics

The dashboard reports real owner-scoped slots, pending approvals, active and
inactive lifecycle counts, recent patches and bounded owner-history events.
An owner with no slots sees a truthful zero-data state; slot creation remains
a host-allocation action because the quota policy is not browser authority.

Slot detail shows the exact slot state/hash/version, quota and configured
model bindings. Display-name, model-binding and lifecycle forms bind current
slot hash plus state/configuration version. Archive and logical deletion are
separate actions and labels. Model bindings are provider-neutral policy, not
model copies of a patch.

Patch cards preserve the exact statement and lifecycle vocabulary from
Steps 29-32, including AWAITING_APPROVAL, APPROVED, COMMITTED, ACTIVE,
SUPERSEDED, REVOKED and DELETED when present. Approval binds the proposal
hash and state hash/version. Revocation binds the exact active patch/state;
logical deletion is available only for the canonical DELETED_PENDING slot
path. Export is produced by the Step 32 service, never reconstructed from the
DOM.

Every page states that Personal Memory is private personalization context,
not an official source or canonical evidence. No UI control publishes to the
Source Registry or makes a private/shared transition.

## HTTP, privacy and browser safety

All mutations are POST-only and require the session's constant-time CSRF
token check; an Origin header, when present, must equal the configured public
origin. Form bodies, field counts, values, IDs, OIDC callback inputs and
return paths are bounded. Each rendered form carries a fresh semantic replay
identity plus the backend-required expected hashes and versions.

Jinja autoescaping treats user, model, Critic and review text as untrusted.
There is no unsafe HTML renderer, auto-open link, execution button or raw
stack/SQL/path error. A strict same-origin CSP, HSTS, frame denial, MIME
sniffing denial, referrer policy, permissions policy and no-store caching are
applied. HTMX is vendored with a pinned SHA-256 and license, so the page has no
runtime CDN dependency or third-party content telemetry.

Lists are fixed and bounded: up to 50 slots, 50 patch projections and 50
owner audit references per server request, with smaller default display
sets. Step 35 adds no vector search and performs no unbounded cross-owner
scan.

The templates use semantic headings/forms, explicit labels, keyboard-sized
controls, visible focus, status regions, reduced-motion support and layouts
for approximately 390 px, 768 px and 1280 px viewports. Destructive actions
require an exact-subject confirmation and use truthful revocation/deletion
wording.

## Step 34 and Step 36 boundaries

Owner UI and reviewer workspace remain separate routes and authorization
surfaces. Owning an object does not grant reviewer authority, and review
roles are not inferred in this package.

Step 36 owns deeper credential separation and Commit Helper authority
hardening. Step 35 consumes the current typed services, introduces no new
credential model, exposes no Commit Helper credential and grants no external
execution authority.
