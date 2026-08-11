# Step 36 Credential Authority Validation 1A

## Purpose and hard safety boundary

This runbook validates credential-purpose separation, Commit Helper
preconditions, database role composition, browser isolation, redaction and
fail-closed missing-secret behavior. It uses fake sentinels and disposable
CockroachDB roles only.

Do not retrieve or print a real secret. Do not rotate a production
credential. Do not mutate AWS or S3. Do not call a model/provider, execute an
external action, run failure injection or start Step 37.

## Repository preflight

From the repository root verify:

```text
git status -sb
git branch --show-current
git rev-parse HEAD
git remote -v
git diff --name-only
git rev-list --left-right --count main...origin/main
```

Require `main`, the expected GitHub remote, a clean starting worktree, no
merge/rebase/cherry-pick/revert/bisect and divergence `0 0`. The exact Step 35
base is `6b2948fc371bbac1b5d48403d65bf7efadd8f56d`; local `HEAD` and
`origin/main` must match it at task start. Verify the Step 35 architecture,
ADR, runbook, evidence, closure, roadmap and AGENTS checkpoint are reachable
from that base, with Steps 36 and 37 not started.

Read `AGENTS.md`, the roadmap, the Step 5/6/7/9/22/27/30/33/34/35 authority
contracts, the Step 36 capability matrix, migration 0018 and its security
manifest before running validation.

## Static inventory without secret values

Inspect committed source, configuration names, role grants and example files.
Do not read a machine-local `.env` or print environment values. Confirm that:

- every `CredentialPurpose` has a complete `CredentialSpec`;
- high-risk consumers use distinct exact environment names;
- no privileged specification is browser-visible;
- `.gitignore` covers local environment/key/credential material;
- `SecretValue` cannot render or serialize its raw value;
- child-process assembly uses `build_minimal_subprocess_environment`; and
- missing dedicated configuration has no generic/admin/master fallback.

Review public frontend inputs under
`src/aioa_memory_kernel/personal_memory_ui`, `package.json` and the pinned
asset metadata. No privileged name, `process.env`, `import.meta.env`, `VITE_`
or `NEXT_PUBLIC_` input may cross the browser boundary.

## Offline schema and contract gates

Run:

```text
python3 -m compileall -q src scripts tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.test_step36_credential_separation -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.test_step36_commit_authority -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.test_step36_secret_redaction -q
python3 scripts/run_cockroachdb_migrations.py --offline-validate
python3 scripts/validate_contracts.py
npm run check:assets
```

The focused suites require exact credential purposes, no admin fallback,
immutable/redacted/non-serializable values, minimal child environments,
complete capability matrix cells, zero browser credential contracts, strict
approval/commit/activation service separation, provider isolation, receipts
as non-bearer integrity objects and secret rejection across audit, owner
export, review and UI surfaces.

Offline migration validation must recognize exactly migration 0018 under
manifest/runner schema 16, both new roles, all Step 36 policies and triggers,
the strengthened existing predicates and no credential value in SQL.

## Static security review

Run the repository's bounded scans from the Step 36 task. Review every hit in
context. Expected names in the typed inventory, fake fixtures and security
documentation are not raw credentials.

At minimum inspect:

```text
rg -n "BYPASSRLS|bypassrls|ALTER ROLE.*BYPASS|CREATE ROLE.*BYPASS" \
  sql src scripts docs || true
rg -n "execute_sql|raw_sql|update_any|set_patch_state|auto_approve|approve_any|BYPASSRLS|admin_database_url|root_database_url" \
  src scripts || true
rg -n "os\.environ|process\.env|print\(.*env|logger\..*env|repr\(.*settings" \
  src scripts . --glob '!/.git/**' || true
rg -l "(sk-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|postgres(ql)?://[^[:space:]]+:[^[:space:]@]+@|cockroachdb://[^[:space:]]+:[^[:space:]@]+@)" \
  . --glob '!/.git/**' --glob '!docs/history/**' || true
```

The secret-shape scan deliberately prints filenames only. Inspect each file
without copying a matching value into the terminal transcript, issue, log,
audit record or validation evidence. Stop and handle it outside this workflow
if a real credential is discovered.

## Controlled security validation

Use only the repository-pinned CockroachDB v26.2.4 binary or pass an exact
owned binary path:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  scripts/run_step36_credential_authority_validation.py
```

The validator must use fake credential sentinels and one owned disposable
runtime/database with bounded LOGIN roles. It applies and replays the
canonical migration chain once, then performs three bounded groups in that
same isolated database:

The validator applies trusted migration transactions and performs service and
role probes through the owned loopback pgwire/SQL boundary. No production DSN
or ambient credential is forwarded.

1. offline credential inventory, missing-secret, child-environment, browser
   and redaction checks;
2. real Step 30 positive exact-owner Commit Helper plus cross-user,
   cross-tenant, receipt/hash and replay negatives, and real Step 34
   reviewer/isolation regressions; and
3. migration replay/catalog validation plus exact and deliberately composed
   LOGIN probes for application, Commit Helper, source publisher, reviewer
   and audit reader.

Accept the database-role matrix only if:

- exact Commit Helper authority succeeds but app and mixed app/helper roles
  fail;
- app cannot update registry publication state or insert a publication event;
- exact publisher can perform only its publication transition and cannot
  commit Personal Memory;
- a mixed publisher role fails its exclusive predicate;
- reviewer cannot commit, activate/change slots, publish or migrate;
- audit reader can read exact owner-private audit rows but cannot append,
  advance a chain head or mutate business state;
- all application, helper, reviewer, publisher and audit-reader roles are
  `NOBYPASSRLS`; and
- RLS tenant/owner contexts reject cross-user, cross-tenant and spoofed
  identities.

Audit append is a typed logical credential boundary and remains atomic with
the exact business transaction. Do not expect a new physical
`mp_audit_appender` role. The physical Step 36 audit role is the read-only
`mp_audit_reader`.

Step 10 ingestion remains a documented normal-runtime residual. The reserved
`DATABASE_URL_INGESTION` name does not prove a physical ingestion role; do
not report one.

The validator emits bounded progress JSON on stderr and one sanitized
canonical JSON result on stdout. Capture only that result for the Step 36
evidence artifact after verifying its digest. It must contain no secret value,
machine-local credential path or production identifier.

## Frontend and downstream redaction checks

Run the exact Step 35 asset check and focused UI/security regression. Scan the
rendered/static package with fake sentinels for the application, Commit
Helper, provider, migrator, reviewer, audit and AWS names. Expected privileged
hits are zero.

Exercise fake sentinel failures through:

- `SecretValue` string/repr/logging;
- provider and database exception normalization;
- Step 33 audit payload/export;
- Step 32 Personal Memory owner export;
- Step 34 review detail/error handling; and
- Step 35 generic UI error rendering.

No raw sentinel may appear. Redaction must preserve bounded reason codes and
hash references without pretending a redacted payload is the original audit
payload.

## Full regression

After the focused and controlled gates pass, run:

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  -m unittest discover -s tests -p 'test*.py' -q
```

Also run focused regressions for Steps 5, 6, 7, 9, 22, 27 and 30-35, including
RLS, persistence, Source Registry publication, S3/Object Lock assumptions,
tenant/user isolation, audit append-only behavior and the Step 35 browser
boundary. No ordinary test may require a real provider key or AWS identity.

## Rotation-readiness review

For each named deployment input, confirm the capability matrix states its
consumer and replacement procedure. A future rotation must provision the
replacement, restart/reload only that consumer, validate positive and
negative capability probes, and revoke the old identity.

Do not perform a production rotation during Step 36. Specifically, do not
retrieve a production value, write a secret into a command line, fall back to
the migrator, or reuse one credential across unrelated consumers.

## Cleanup and acceptance

The controlled runner must drop every owned temporary database and LOGIN
role, stop every owned CockroachDB process, close its ports, remove temporary
stores and report no force kill. It must leave no provider call, AWS/S3
mutation, production rotation or failure-injection process.

Before staging, run `git diff --check`, inspect the complete changed-file list
and confirm every path is attributable to Step 36. Acceptance requires all
focused, offline, controlled and full-regression gates to pass, zero secret
leakage, no broad runtime `BYPASSRLS`, no master/admin fallback and no Step 37
implementation.

`Step 37: NOT STARTED`.
