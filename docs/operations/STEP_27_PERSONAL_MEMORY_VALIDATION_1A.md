# Step 27 Personal Memory Validation 1A

## Repository preflight

```bash
git status -sb
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count main...origin/main
git log -1 --format='%H%n%s' origin/main
```

Require clean `main`, local/origin equality, Step 26 complete and pushed, and
Steps 27 and 28 not started before implementation. Verify the exact Step 26
closure, roadmap, and AGENTS checkpoint from the frozen base.

## Static and unit validation

```bash
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest tests.test_step27_personal_memory_persistence -q

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 scripts/validate_contracts.py
```

The focused suite covers immutable slot/quota/binding/export contracts, exact
replay and replay conflict, lifecycle transitions, hard quota boundaries,
provider-neutral bindings, owner isolation, canonical export, logical
deletion, migration/RLS declarations, authority negatives, and Step 28+
boundaries.

## Controlled CockroachDB validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/run_step27_personal_memory_validation.py
```

The runner verifies and copies the repository-pinned CockroachDB v26.2.4
binary into an owned temporary directory. It creates one disposable in-memory
node and database, applies all 11 migrations, replays all 11 migrations, and
creates one temporary least-privileged application login. It never targets a
production database.

Expected live cases are:

- one empty slot with zero item, patch, and byte usage;
- exact slot-create replay;
- total-slot quota at its exact limit and an over-limit rejection;
- two distinct exact-model bindings and a third-binding rejection;
- `CONFIGURED -> ACTIVE -> SUSPENDED -> ARCHIVED`;
- deterministic owner-only configuration export;
- `DELETED_PENDING -> DELETED` logical tombstone;
- owner visibility plus zero rows for a same-tenant other user and another
  tenant; and
- RLS and FORCE RLS on spaces, model bindings, and quota policies.

Compare the canonical result with
`docs/evidence/personal-memory/step27-personal-memory-persistence-validation.json`
and recompute `validation_digest` with the repository canonical hash helper.

## Static boundaries

```bash
rg -n "Gemma|gemma" \
  src/aioa_memory_kernel/personal_memory \
  tests/test_step27_personal_memory_persistence.py || true

rg -n \
  "CorrectionCandidate|critic.*bridge|patch_proposal|AWAITING_APPROVAL|commit_helper|activate_patch|active_patch_retrieval|shared_promotion" \
  src/aioa_memory_kernel/personal_memory \
  tests/test_step27_personal_memory_persistence.py || true

rg -n \
  "subprocess|os\.system|shell=True|execute_action|external_action|approval|commit_helper|control_write" \
  src/aioa_memory_kernel/personal_memory || true
```

Review boundary-test and explanatory hits. Functional Gemma coupling,
candidate/patch/approval/activation/retrieval/sharing behavior, shell access,
and external-action authority are forbidden.

## Failure semantics and cleanup

Identity, RLS, owner, quota, version, transition, hash, or replay mismatches
roll back and return a bounded typed reason. A model cannot submit a mutation
command. Exports never include credentials or another user's data.

The runner drops its disposable database and validation role, gracefully
stops the exact owned process, verifies closed ports, and removes the
temporary store. Expected cleanup has `force_kill_used=false`. No AWS, S3,
provider, model, web, patch, approval, or execution action occurs. Step 28
remains NOT STARTED.
