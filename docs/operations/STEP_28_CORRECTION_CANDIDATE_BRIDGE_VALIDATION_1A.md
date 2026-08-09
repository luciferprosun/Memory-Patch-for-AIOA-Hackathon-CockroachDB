# Step 28 Correction Candidate Bridge Validation 1A

## Repository preflight

```bash
git status -sb
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count main...origin/main
git log -1 --format='%H%n%s' origin/main
```

Require clean `main`, local/origin equality, Step 27 complete and pushed, and
Steps 28 and 29 not started before implementation. Verify the exact Step 27
architecture, ADR, runbook, evidence, closure, roadmap, and AGENTS checkpoint
from the frozen base.

## Step 27 slot prerequisite

Create owner-scoped Step 27 slots under disposable Tenant A / User A context.
Candidate-positive fixtures use only exact `CONFIGURED` or `ACTIVE` slots and
bind the slot/configuration hash and version. Keep separate archived and
delete-pending fixtures for fail-closed state tests. Never reactivate a slot
through candidate intake.

## Static and unit validation

```bash
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest tests.test_step28_correction_candidate_bridge -q

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 scripts/validate_contracts.py
```

The focused suite covers envelope immutability and hashes, exact Kernel and
Critic producer adapters, slot/owner/route lineage, candidate bounds, hard
quota, idempotent replay, deterministic exact deduplication, RLS/FORCE RLS,
owner isolation, non-authority, and the Step 29+ boundary. A Hub adapter is
`NOT_REQUIRED` because the repository has no canonical Hub runtime producer.

## Controlled CockroachDB validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/run_step28_correction_candidate_bridge_validation.py
```

The runner uses the repository-pinned CockroachDB in an owned disposable
runtime, applies all migrations including 0012, replays the migration chain,
and uses a least-privileged application role. It never targets production.

Expected cases include:

- Kernel and synthetic Critic Loop submissions accepted as `DETECTED`;
- exact idempotency replay and exact semantic duplicate reuse;
- completed replay after slot archival and duplicate-receipt provenance;
- changed content under one idempotency identity rejected;
- real database count/byte ceilings, at-limit replay, quota-epoch
  serialization, a two-transaction race for the final quota position, and
  deterministic denial;
- Python/Cockroach canonical owner-scope hash parity and exact current
  materialized slot-hash binding;
- unknown, cross-user, cross-tenant, archived, and delete-pending targets
  denied;
- User B cannot submit to or read User A's candidates;
- RLS and FORCE RLS remain enabled on the reused proposal carrier; and
- zero approval, commit, activation, canonical-evidence promotion, provider,
  model, web, AWS, S3, or external-execution actions.

If no repository Critic fixture exists, validation must report
`REAL_CRITIC_FIXTURE=NO` and `SYNTHETIC_CRITIC_FIXTURE=YES`; it must not invent
a real integration claim.

Compare the canonical result with
`docs/evidence/personal-memory/step28-correction-candidate-bridge-validation.json`
and recompute its `validation_digest` using the repository canonical hash
helper.

## Static authority and later-step checks

```bash
rg -n \
  "approve|approval|commit_helper|activate_patch|ACTIVE|execution|external_action|control_write" \
  src/aioa_memory_kernel/personal_memory \
  src/aioa_memory_kernel/corrections || true

rg -n \
  "EVIDENCE_BOUND|VALIDATED|AWAITING_APPROVAL|PersonalMemoryPatchProposal|active_patch_retrieval|shared_promotion|supersession|revocation" \
  src/aioa_memory_kernel \
  tests/test_step28_correction_candidate_bridge.py || true

rg -n \
  "NOOA|OpenShell|NVIDIA|ExecutionAuthorizationDecision|execute_action|subprocess|os\.system|shell=True" \
  src/aioa_memory_kernel/personal_memory \
  src/aioa_memory_kernel/corrections || true
```

Review inherited enums and explanatory boundary references. New Step 28 code
must have no proposal transition, evidence validation, approval, commit,
activation, retrieval, shared promotion, provider/tool call, or external-agent
runtime integration.

## Failure semantics and cleanup

Identity, source, lineage, owner, slot-state, quota, hash, or replay conflicts
roll back with bounded typed reasons. Candidate intake never changes the slot
or returns later lifecycle authority.

The controlled runner drops its disposable role/database, stops only the
owned process, verifies closed ports, and removes the temporary store. No
production data or secret is used. Step 29 remains NOT STARTED.
