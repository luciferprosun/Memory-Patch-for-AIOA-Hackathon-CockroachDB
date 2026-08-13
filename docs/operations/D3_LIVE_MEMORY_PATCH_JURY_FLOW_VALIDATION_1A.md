# D3 Live Memory Patch Jury Flow Validation 1A

## Scope

D3 validates the current jury browser flow without AWS, a public listener, a
real OIDC provider, production database mutation, or paid model call. It uses
the canonical application contracts with controlled adapters and preserves the
R7 runtime and D2 legacy classification.

The architecture is documented in
[`LIVE_MEMORY_PATCH_JURY_FLOW_1A.md`](../architecture/LIVE_MEMORY_PATCH_JURY_FLOW_1A.md),
the click path in
[`D3_MEMORY_PATCH_JURY_CLICK_PATH_1A.md`](../demo/D3_MEMORY_PATCH_JURY_CLICK_PATH_1A.md),
and the machine-readable result in
[`d3-live-memory-patch-jury-flow-1a.json`](../evidence/demo/d3-live-memory-patch-jury-flow-1a.json).

## Canonical local start

From the repository root, with all R7 server-side settings supplied:

```bash
.venv/bin/python scripts/run_demo_runtime_1a.py serve
```

The default loopback URL is `http://127.0.0.1:8000`; hosted mode requires the
configured HTTPS public origin. After the existing OIDC login, open
`/memory/demo`. The current mode is selected by default. Legacy archival
metadata is selectable only when `AIOA_DEMO_LEGACY_MODE_ENABLED=1`.

Required configuration names are inherited unchanged:

- `AIOA_RUNTIME_MODE`, `AIOA_RUNTIME_BIND_HOST`, `AIOA_RUNTIME_PORT`,
  `AIOA_RUNTIME_PUBLIC_ORIGIN`;
- `DATABASE_URL_APP`, `DATABASE_URL_MIGRATOR` with hosted `sslmode=verify-full`;
- `AIOA_OIDC_ISSUER`, `AIOA_OIDC_CLIENT_ID`,
  `AIOA_JUDGE_ALLOWED_OIDC_SUBJECTS`;
- `OPENROUTER_API_KEY`, `AIOA_DEMO_PROVIDER_BUDGET_EPOCH`,
  `AIOA_DEMO_PROVIDER_TENANT_ID`;
- the existing `AIOA_DEMO_MAX_*` and `AIOA_DEMO_PROVIDER_MAX_*` request,
  call, concurrency, queue, token, response, and timeout limits.

No secret value belongs in a command line, document, browser field, query
string, template, or evidence record.

## Guided validation

1. Authenticate through the existing server-derived owner/judge boundary.
2. Open `/memory/demo` and confirm `CURRENT / EVIDENCE-BOUND`.
3. Select the primary `primary-entry-into-force` guided case.
4. Submit once. Refresh and polling do not create another paid call.
5. Observe the actual statuses across the 14-stage trace.
6. If Draft V1 is already correct, accept `CORRECTION_NOT_REQUIRED` and select
   only the declared backup if the demonstration requires a correction.
7. If a correction is verified, confirm the Evidence Bundle, Correction
   Packet, Draft V2, Step 25 result, and Step 26 Verified Answer hashes.
8. In the Personal Memory panel, approve only a real existing
   `AWAITING_APPROVAL` record. The checkbox begins unchecked and approval does
   not itself claim commit/activation.
9. Confirm any displayed `ACTIVE` patch carries its actual hash/model binding
   and remains private/non-canonical.
10. Switch to Critical Prompt Loop and confirm `LEGACY / ORIGIN`, `NOT LIVE`,
    `NOT A REPLAY`, and zero execution authority.

## Provider-free D3 campaign

Run:

```bash
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python scripts/run_d3_live_memory_patch_jury_flow_validation.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest tests.test_d3_live_memory_patch_jury_flow -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest \
  tests.test_demo_cockpit_shell \
  tests.test_demo_cockpit_security \
  tests.test_demo_cockpit_loopback \
  tests.test_demo_legacy_archive \
  tests.test_demo_legacy_compatibility_security \
  tests.test_step35_personal_memory_ui \
  tests.test_step35_personal_memory_ui_security \
  tests.test_step36_commit_authority \
  tests.test_step36_credential_separation \
  tests.test_step36_secret_redaction \
  tests.test_step38_german_law_e2e \
  tests.test_step38_real_retrieval \
  tests.test_step39_critic_authority \
  tests.test_step43_documentation_demo \
  tests.test_demo_runtime_composition \
  tests.test_demo_runtime_health \
  tests.test_demo_runtime_provider_guard -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python scripts/validate_contracts.py

node scripts/check_step35_ui_assets.mjs
git diff --check
```

Because D3 changes shared runtime composition and UI modules, the final gate
also runs full `unittest discover` before commit.

## Expected safety results

- Current mode default: yes.
- Legacy/current authority bridge: none.
- Draft V1 evidence blind: yes.
- Verified Answer gated by Step 25 and Step 26: yes.
- Paid call on page load, mode switch, or polling: zero.
- Duplicate submit: same owner/session/idempotency projection.
- Cross-owner and cross-session run access: denied.
- Browser provider, database, OIDC, and Commit Helper secrets: zero.
- Personal Memory auto-approval: zero.
- New migration: none.
- AWS/public deployment mutation: zero.

## Failure and cleanup

A provider, budget, retrieval, temporal, or verification failure yields a
bounded reason code and no unverified answer. A worker exception becomes
`INTERNAL_RUNTIME_FAILURE` without a traceback in HTML. Closing the runtime
stops the owned one-worker coordinator, waits only for already bounded work,
and clears ephemeral projections. Canonical evidence and Personal Memory are
untouched.

D3 does not establish external OIDC uptime, a production CockroachDB endpoint,
or hosted network behavior. Those remain deployment and D5 hosted-proof gates.

## Measured closure result

The final D3 campaign completed with 10/10 focused D3 tests, 347/347 focused
cross-step regression tests, and 2337/2337 tests in full repository discovery.
Compilation, contract validation, UI asset validation, secret scanning, XSS
checks, and `git diff --check` passed. The campaign used only the controlled
fake provider and made zero paid calls. It touched zero AWS or public
deployment resources.
