# Step 29 Personal Memory Patch Validation 1A Runbook

## Preflight

Run from the repository root on `main`. Require a clean worktree, no active
Git operation, exact expected remote, and `main...origin/main = 0 0`. Confirm
Step 28 is complete and pushed while Steps 29 and 30 are not started. Freeze
`origin/main` as the Step 29 start SHA.

Never point validation at production data. The controlled runner creates one
owned disposable CockroachDB database and a least-privileged temporary role.
It does not call a provider/model, retrieve evidence, browse, access AWS/S3,
approve, commit, activate, or execute.

## Focused validation

```bash
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest tests.test_step29_personal_memory_patch_proposal -q

python3 scripts/run_cockroachdb_migrations.py --offline-validate
python3 scripts/validate_contracts.py
```

The focused suite checks immutable/hash-bound contracts, strict JSON
roundtrip, exact state edges, canonical evidence binding, dedup/conflict/
freshness gates, owner/slot/quota/model checks, service idempotency,
optimistic concurrency, migration/RLS security, and Step 30 absence.

## Controlled database validation

```bash
python3 scripts/run_step29_personal_memory_patch_validation.py
```

The runner pins the repository CockroachDB binary and validates all thirteen
migrations plus idempotent replay. It creates a real Step 27 owner slot,
persists a real Step 28 candidate based on a sanitized Step 20-26 pipeline,
then performs all four Step 29 transitions through the application role.

Expected states are exactly `PROPOSED`, `EVIDENCE_BOUND`, `VALIDATED`, and
`AWAITING_APPROVAL`, with four transition records. Exact command replay must
reuse existing state. Direct skipping or entry into `ACTIVE` must fail.

The negative matrix covers exact duplicate, deterministic contradiction,
stale and insufficient evidence, cross-user and cross-tenant reads/updates,
archived and delete-pending targets, and absence of Step 30 authority.
RLS/FORCE RLS must be true for proposal, transition, and slot carriers.

## Full regression

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 scripts/validate_contracts.py
```

Also retain focused Step 17 and Step 20-28 regressions, Step 5 RLS, Step 6
persistence/idempotency, Personal Memory authority invariants, tenant/user
isolation, and contract serialization.

## Failure semantics and cleanup

Any candidate/evidence hash mismatch, owner/tenant/slot mismatch, invalid
state edge, stale/conflicting/insufficient evidence, duplicate, quota/model
failure, or RLS failure is fail closed. Do not stage or commit on failure.

The controlled runner drops its database and temporary role, stops only its
owned process, closes ports, and removes its temporary store. A cleanup
failure is itself a failed validation. No secret value or machine-specific
path belongs in committed evidence.
