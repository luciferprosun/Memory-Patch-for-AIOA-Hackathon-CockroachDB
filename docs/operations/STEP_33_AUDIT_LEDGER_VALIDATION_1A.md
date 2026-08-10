# Step 33 Audit Ledger Validation 1A

## Purpose and safety

This runbook validates the append-only ledger, transactional hash chain,
tamper verifier and bounded proof-carrying owner export. It uses sanitized
Step 30/32 receipt hashes and one owned disposable CockroachDB. It makes no
model/provider, web, AWS/S3, external-action or production-database call and
implements no Step 34 review workspace or Step 35 UI.

## Preflight

From the repository root require `main`, clean worktree at task start, the
expected Git remote, `main...origin/main=0 0`, no active Git operation and
exact Step 32 base `355a790b50a6412adcf64dd0a463219574a3f849`. Verify the
Step 32 architecture, ADR, runbook, evidence, closure, roadmap and AGENTS
checkpoint are reachable from that base; Step 33 and Step 34 must be not
started.

Verify the repository-pinned CockroachDB v26.2.4 binary and SHA-256 through
the existing external-volume bootstrap. Never supply a production database.

## Static and focused checks

Run:

```text
python3 -m compileall -q src scripts tests
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_step33_audit_ledger -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_step33_audit_export -q
python3 scripts/run_cockroachdb_migrations.py --offline-validate
python3 scripts/validate_contracts.py
```

Review the Step 33 package and SQL for closed types, canonical hashing,
bounded payload/export sizes, exact tenant/owner predicates, parameterized
queries, RLS/FORCE RLS, no BYPASSRLS and no audit-event UPDATE/DELETE API.

## Controlled disposable validation

Run:

```text
PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_step33_audit_ledger_validation.py
```

The script copies and verifies the pinned binary, starts one owned runtime,
creates a uniquely named database and no-BYPASSRLS validation role, applies
all 16 migrations, reruns them idempotently and checks the live Step 33
catalog.

It appends representative Step 30 approval/commit/activation and Step 32
supersession/revocation/export/deletion/shared-promotion receipt hashes. No
Personal Memory statement is stored. It then proves:

- exact replay returns the original event and changed replay is denied;
- exact, conflicting and unique concurrent appends on a separate sanitized
  owner chain yield one contiguous verified sequence without making the
  representative export hash scheduler-dependent;
- genesis, predecessor links, reconstructed hashes and head verify;
- same-tenant cross-user and cross-tenant RLS reads and inserts are denied;
- ordinary UPDATE and DELETE are denied and row count does not change;
- owner export is bounded, ordered, anchored, verified and hash-only;
- cross-user and cross-tenant export fail closed;
- payload, type, subject, predecessor, deletion, reordering, forgery,
  duplicate sequence and head tampering are all detected;
- exported representation contains no secret-shaped value or machine path;
  and
- no audit operation gains business authority.

The script emits progress as bounded JSON to stderr and one canonical result
to stdout. A failure still runs exact database, role, process, port and
temporary-store cleanup.

## Full regression and static gates

Run full discovery plus focused Step 17 and Step 20-32, persistence, RLS,
authority, tenant/user and serialization regressions:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test*.py' -q
```

Run the prompt-specified Step 34 leakage, secret and mutation searches.
Review each hit in context; comments and closed vocabulary do not authorize a
later-step feature. Run `git diff --check`, inspect every changed path and
stage only Step 33 files.

## Cleanup and acceptance

Accept only a result with database and role removed, process exited, ports
closed, temporary store removed and `force_kill_used=false`. Recompute the
validation digest after removing its own field and require equality.
The exact owned CockroachDB PID receives SIGTERM and a bounded 120-second
busy-node grace period before cleanup fails closed; no broad process kill or
forced kill is permitted.

The final repository gate requires zero failures/errors, one Step 33 Git
closure commit, successful non-force push, clean status and local/remote
divergence `0 0`.

## Later-step guard

Confirm `step34_started=false`, `human_review_workspace=0` and
`personal_memory_ui=0`. The typed future `REVIEW_SERVICE` actor is descriptive
only and does not implement reviewer access, queueing, decisions or UI.
