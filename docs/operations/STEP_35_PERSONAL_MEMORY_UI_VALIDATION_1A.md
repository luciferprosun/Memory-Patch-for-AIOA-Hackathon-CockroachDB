# Step 35 Personal Memory UI Validation 1A

## Purpose and safety

This runbook validates the owner UI over exact Step 27-33 services. It uses
no production database, production identity provider, model/provider call,
web retrieval, AWS/S3 mutation, external execution or Commit Helper browser
endpoint. The controlled validator owns and removes one disposable
CockroachDB runtime.

## Preflight

From the repository root require `main`, a clean worktree at task start, the
expected GitHub remote, `main...origin/main=0 0`, no active Git operation and
exact Step 34 base `9dce1e9192e98d38f3af64d736effa3b017788b8`.

Verify that the Step 34 architecture, ADR, runbook, evidence, closure,
roadmap and AGENTS checkpoint are reachable from that commit; Steps 35 and 36
must be not started. Use only the pinned repository CockroachDB v26.2.4
binary and disposable prefixes accepted by the migration guard.

## Runtime dependencies and assets

Create an isolated environment and install exact versions from:

```text
requirements-ui.txt
```

No JavaScript package installation is required for validation. HTMX 2.0.8 is
vendored with its license. Verify its exact asset digest with:

```text
npm run check:assets
```

The result must be
`22283ef68cb7545914f0a88a1bdedc7256a703d1d580c1d255217d0a50d31313`.

## Focused validation

Run:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_step35_personal_memory_ui \
  tests.test_step35_personal_memory_ui_security -q
python3 -m compileall -q src scripts tests
npm run check:assets
python3 scripts/validate_contracts.py
```

The focused suite covers OIDC/PKCE, secure opaque sessions, CSRF/origin and
body bounds, XSS escaping, IDOR, safe errors, deliberate hash-bound approval,
slot and model-binding actions, receipt-driven lifecycle views, destructive
confirmation, no GET mutations, accessibility/responsive contracts and no
browser credential/Commit Helper exposure.

## Controlled disposable validation

Run:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  scripts/run_step35_personal_memory_ui_validation.py
```

The runner verifies and copies the pinned binary, creates one owned runtime,
applies and replays all migrations and verifies RLS/FORCE RLS on the reused
slot, proposal, patch, lifecycle and audit tables. It creates real sanitized
Step 27-32 fixtures for AWAITING_APPROVAL, ACTIVE, SUPERSEDED, REVOKED and
logical DELETED states plus one Step 33 owner audit event.

The primary path authenticates a synthetic owner through the same OIDC/PKCE
application boundary, renders the dashboard, calls the exact Step 30 approval
service, rejects the stale replay, adds an exact provider-neutral model
binding, performs Step 32 export/revocation/logical deletion through the UI
adapter and renders resulting backend state. It then proves direct-object and
mutation denial for User B, tenant isolation, CSRF and absence of GET
mutation routes.

Progress is bounded JSON on stderr and stdout is one canonical sanitized JSON
document. On success copy that exact stdout to
`docs/evidence/ui/step35-personal-memory-ui-validation.json` and recompute the
digest after excluding `validation_digest`.

## Full regression and static gates

Run full discovery and the focused Step 17/27-34, RLS, persistence,
authority, tenant/user, audit and serialization regressions:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test*.py' -q
```

Review Step 36 leakage, authority escalation and secret searches from the
task. Expected contract names and safe documentation references are not
credentials or authority. Run `git diff --check`, inspect every changed path
and stage only Step 35 files.

## Cleanup and acceptance

Accept only when the validation database and roles are removed, the owned
CockroachDB PID exits, all three ports close, the temporary store is removed
and no force kill is used. Verify there is no remaining Step 35 validator or
CockroachDB process.

The final gate requires focused tests, asset check, compile, full regression,
contract validator and controlled validation all green; one Step 35 closure
commit; non-force push; clean status; and local/remote divergence `0 0`.

Confirm that owner/reviewer surfaces remain separate and
`Step 36: NOT STARTED`.
