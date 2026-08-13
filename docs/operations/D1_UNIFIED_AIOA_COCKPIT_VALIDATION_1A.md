# D1 Unified AIOA Cockpit Validation 1A

## Scope and status

D1 adds the unified presentation shell at authenticated route
`GET /memory/demo`. It reuses the one R7 ASGI runtime and preserves all
existing `/memory` owner-workspace routes. No provider call, CockroachDB
business mutation, migration, AWS operation, or public deployment is part of
D1.

The architecture contract is documented in
[`UNIFIED_AIOA_DEMO_COCKPIT_1A.md`](../architecture/UNIFIED_AIOA_DEMO_COCKPIT_1A.md),
and the machine-readable result is
[`d1-unified-aioa-cockpit-validation-1a.json`](../evidence/demo/d1-unified-aioa-cockpit-validation-1a.json).

## Runtime configuration

The canonical command remains:

```bash
.venv/bin/python scripts/run_demo_runtime_1a.py serve
```

D1 adds one non-secret setting:

| Variable | Values | Default | Meaning |
|---|---|---|---|
| `AIOA_DEMO_LEGACY_MODE_ENABLED` | `0` or `1` | `0` | Makes the Legacy / Origin view selectable; it grants no execution or authority |

All R7 requirements remain mandatory: runtime mode, loopback/host bind and
port, public origin, OIDC issuer/client, judge allowlist, separate application
and migrator database credentials, verified CockroachDB TLS, server-side
provider credential, and the durable provider-budget epoch. This document
names configuration only and contains no secret value.

Hosted mode must never use a test identity, in-memory session fallback,
plaintext database connection, browser provider key, or unguarded provider.
Enabling the legacy view does not change any of those requirements.

## Operator validation

From the repository root:

```bash
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest \
  tests.test_demo_cockpit_shell \
  tests.test_demo_cockpit_security \
  tests.test_demo_cockpit_loopback -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest \
  tests.test_step35_personal_memory_ui \
  tests.test_step35_personal_memory_ui_security \
  tests.test_demo_runtime_composition \
  tests.test_demo_runtime_health \
  tests.test_demo_runtime_judge_auth \
  tests.test_demo_runtime_durable_sessions \
  tests.test_demo_runtime_provider_guard \
  tests.test_demo_runtime_launcher -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python scripts/validate_contracts.py

node scripts/check_step35_ui_assets.mjs
git diff --check
```

The focused loopback test binds an ephemeral port on `127.0.0.1`, starts the
actual composed FastAPI app with explicit test adapters, checks
`/health/live`, `/health/ready`, and authenticated `/memory/demo`, then shuts
down and verifies that the provider call count is zero. It is a controlled auth
test harness, not external OIDC uptime proof and not a public service.

## Expected UI result

1. An unauthenticated request follows the existing OIDC login behavior.
2. After server-derived authentication, `/memory/demo` selects Memory Patch by
   default and visibly states `CURRENT / EVIDENCE-BOUND`.
3. The page exposes the D3-ready stage sequence without claiming that a run
   occurred.
4. With the legacy flag at `0`, the Critical Prompt option is visibly disabled
   and current mode works normally.
5. With the flag at `1`, selecting the legacy option performs only a GET and
   shows `LEGACY / ORIGIN - VIEW ONLY`, `NOT LIVE`, and advisory authority.
6. Switching modes causes zero Personal Memory mutation and zero provider
   call.

## Measured D1 result

| Gate | Result |
|---|---|
| Focused cockpit behavior/security/loopback | `11/11 PASS` |
| Step 35 and R7 runtime regression | `99/99 PASS` |
| Credential/security/documentation regression | `42/42 PASS` |
| Total unit tests in the D1 campaign | `152/152 PASS` |
| Controlled loopback | live `200`, ready `200`, cockpit `200`, clean shutdown |
| Paid provider calls | `0` |
| Personal Memory business mutations from mode switching | `0` |
| Secret leakage | `0`; browser privileged/rendered hits `0` |
| Contract validator | `PASS`; 5 schemas, 4 fixtures, 2 unrelated HAT manifests |
| Step 35 asset checker | `PASS`; vendored HTMX SHA-256 unchanged |
| Markdown links | `169/169 PASS` |
| Python compilation and `git diff --check` | `PASS` |

The controlled identity was an explicit test harness with the same
server-side owner binding, not a claim of external OIDC uptime. No production
resource or AWS resource was touched.

## Failure and rollback behavior

- An invalid legacy flag fails typed configuration with `CONFIG_INVALID`.
- An unknown mode value falls back to current mode with a bounded safe notice;
  the unknown text is not reflected.
- A missing legacy execution artifact cannot break current mode because D1
  imports none.
- If cockpit rendering fails, the existing sanitized UI error handler and
  security headers remain in force.
- Rollback is the single D1 commit; no schema or durable state rollback is
  necessary because D1 adds no migration or business mutation.

## D1 limitations and D2 handoff

D1 is a shell, not the final live jury trace. It does not add prompt input or
run actions, connect a legacy controller, attach a historical replay, or
orchestrate the current Golden Path. The legacy classification remains
`LEGACY_VIEW_ONLY`. D2 must either preserve that view or prove an exact
sanitized replay/live adapter through the current provider guard. D3 owns the
current Memory Patch live trace.
