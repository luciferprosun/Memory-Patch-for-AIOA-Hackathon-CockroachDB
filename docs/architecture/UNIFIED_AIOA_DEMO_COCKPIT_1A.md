# Unified AIOA Demo Cockpit 1A

## Outcome

D1 adds one jury-facing cockpit to the existing FastAPI/Jinja2/HTMX
application. It does not add a second web server, identity system, provider
client, database authority, or business workflow. The canonical ASGI target
remains `aioa_memory_kernel.demo_runtime.asgi:app`, and the authenticated
cockpit route is `GET /memory/demo`.

The two presentation modes are deliberately unequal:

- **Memory Patch - Current** is the default, evidence-bound production
  authority path. D1 presents its typed stage contract; D3 owns the live
  one-page Golden Path orchestration.
- **Critical Prompt Loop - Legacy / Origin** is an optional historical view,
  disabled by default. D1 makes no live or replay claim and exposes no legacy
  mutation or provider endpoint.

D1 is based on R7 commit
`745d4d180ccf2c7ddbb6eeb2b7a57c091eb016dd`. It does not create Step 44.

## D0 decisions and AOIA-Core provenance

D0 classified the historical runtime implementations as references, not
production dependencies. D1 cleanly ports only the useful visual language:
an operator cockpit, bounded status cards, a staged result panel, and
hash/receipt-oriented wording. No AOIA-Core byte was copied into this change.

| Historical surface | Exact source identity | D1 decision |
|---|---|---|
| Web Orchestra / Critic cockpit | AOIA-Core `eda1449e6a63b6a41d8bc16409aa31a128176804`; `runtime/webapp.py`, `runtime/orchestra_live_smoke_cli.py`, `runtime/run_web.sh` | `PORT` the cockpit/status concepts; reject the server and controller |
| Session view / Critic audit presentation | AOIA-Core `46695cde96d12a52e20bea82ebe2e1798b7451fd`; `runtime/webapp.py` blob `ec0952c75ecb884902fb5e7874c8f4c936b7a2da` | `PORT` bounded presentation motifs only |
| Desktop Critical Review | AOIA-Core `51abb9faab2d07d21003a345c747b90b8eac5703` and the completed five-call revision at `5ec74f85256c260dadbc795143eb132b4119aab6`; `apps/aoia_desktop_demo/critical_review.py` final blob `07fabcf104eac4de63aa7f60d6af79fcdd7c37e4` | `KEEP AS LEGACY REFERENCE`; no execution in D1 |
| Desktop cockpit state and window | AOIA-Core `5ec74f85256c260dadbc795143eb132b4119aab6`; `ui/cockpit_state.py` blob `8960081a8e21ee76d59607afe5d235aec928d949`, `ui/main_window.py` blob `b2d08e863a34512e93baf11b8114cedc2a183725` | `PORT` the three-card visual idea; reject Tkinter |
| Legacy ThreadingHTTPServer, Tkinter, OpenRouter client, retry/controller logic | Same sources above | `REJECT` |

D0 found no exact, provenance-bound historical execution trace that could be
truthfully exposed as a replay and did not approve a live compatibility
adapter. The resulting classification is `LEGACY_VIEW_ONLY`. D2 must retain
that classification unless it adds a separately verified, sanitized and
hash-bound source artifact through the current security boundary.

## One-application architecture

```text
Browser
  -> existing OIDC Authorization Code + PKCE
  -> existing server-side owner/judge session
  -> existing create_personal_memory_app FastAPI tree
       -> GET /memory/demo
          -> CockpitShell (pure immutable projection)
             -> Memory Patch current view (default)
             -> Legacy / Origin view (optional, config-gated)
       -> existing /memory owner workspace and state-changing routes
  -> existing CockroachDB runtime and provider guard only through services
```

The new `demo_cockpit` package contains immutable and bounded view models plus
a pure `CockpitShell.project()` function. It receives only safe display
identities. Templates never receive database connections, provider clients,
credentials, service objects, or authority-bearing settings.

`personal_memory_ui/cockpit_routes.py` registers exactly one authenticated
GET route on the existing app. Mode selection is a bounded query parameter;
it is presentation state, not a grant and not a mutation. The page uses the
same cookie, owner principal, CSP, security headers and Jinja autoescaping as
the Step 35 owner workspace.

## Typed presentation state

- `CockpitMode`: current Memory Patch or legacy Critical Prompt Loop.
- `CockpitExecutionKind`: `LIVE` or `HISTORICAL_VIEW`. It describes the
  surface, not an execution receipt.
- `CockpitRunState`: `IDLE`, `RUNNING`, `COMPLETED`, or `FAILED`.
- `CockpitRuntimeStatus`: bounded non-secret runtime identity.
- `CockpitStageSummary`: bounded label, status, detail and authority note.
- `LegacyModeStatus`: config availability and truthful legacy classification.
- `CockpitView`: one immutable template projection.

All D1 pages remain `IDLE`. The current page says it is ready for D3 rather
than fabricating a live run. The legacy page says `VIEW ONLY`, `NOT LIVE`, and
that no verified replay is attached.

## Authority firewall

| Capability | Current Memory Patch | Legacy / Origin D1 view |
|---|---:|---:|
| Read current typed presentation contract | Yes | No |
| Trigger a provider call in D1 | No | No |
| Write canonical evidence | Existing services only | No |
| Change route or HAT | Existing policy only | No |
| Approve Personal Memory | Existing owner route only | No |
| Commit or activate Personal Memory | Existing separated services only | No |
| Reset/bypass provider budget | No | No |
| Bypass OIDC, judge policy or owner binding | No | No |

The legacy shell has no backend reference, provider reference, database
reference, Commit Helper reference, or POST route. Turning its display flag on
cannot change owner, tenant, route, evidence, audit, approval, patch, or budget
state. If it is disabled or unavailable, current Memory Patch remains intact.

## UI and security properties

- Jinja autoescaping remains enabled for HTML/XML.
- Model, user and historical text are treated as untrusted display data.
- No unsafe HTML rendering, inline scripts, `innerHTML`, browser storage, CDN,
  or legacy JavaScript was introduced.
- The existing self-only CSP, HTMX no-eval configuration, TrustedHost,
  `Secure`/`HttpOnly`/`SameSite` session cookie and CSRF/origin checks remain.
- The mode selector is keyboard-operable, explicitly labelled, and uses
  `aria-current`/`aria-disabled` rather than color alone.
- Layout breakpoints at 800 px and 520 px preserve the workflow, status and
  observer content on narrow screens; reduced-motion behavior remains.

## Local and hosted consistency

Local rehearsal and future AWS hosting use the same ASGI app, route, templates
and static stylesheet. Only typed configuration changes: bind/port, public
origin, OIDC settings, CockroachDB endpoints and server-side credentials. The
legacy view is controlled by `AIOA_DEMO_LEGACY_MODE_ENABLED`; its default is
`0`, and only the exact values `0` and `1` are accepted.

The canonical start command remains:

```bash
.venv/bin/python scripts/run_demo_runtime_1a.py serve
```

Operational checks and the D2 boundary are recorded in
[`D1_UNIFIED_AIOA_COCKPIT_VALIDATION_1A.md`](../operations/D1_UNIFIED_AIOA_COCKPIT_VALIDATION_1A.md).

## D2 boundary

D2 may add a provenance-bound legacy compatibility or replay layer only
through this shell and the existing auth/provider-guard boundaries. It must
not import `runtime/webapp.py`, Tkinter, the old provider client, or any old
unauthenticated endpoint. Until D2 proves a stronger truthful classification,
the only allowed result is `LEGACY_VIEW_ONLY`.
