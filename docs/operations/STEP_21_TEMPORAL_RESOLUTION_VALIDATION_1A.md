# Step 21 Temporal Resolution Validation 1A

## Constraints

All Step 21 code and validation are offline, deterministic, and bounded. They
need no database, model runtime, provider, network acquisition, AWS, or S3.
The resolver reads only already-verified Step 20 contracts and repository
evidence fixtures.

## Preflight and baseline

```bash
git status -sb
git rev-list --left-right --count main...origin/main

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python3 -m unittest discover -s tests -p 'test*.py' -q
python3 scripts/validate_contracts.py
python3 -m compileall -q src scripts tests
```

The branch must be `main`, the worktree clean before implementation, and the
local and remote heads identical. Step 20 must be complete and Step 22 not
started.

## Focused Step 21 suite

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python3 -m unittest tests.test_step21_temporal_resolution -q
```

The suite verifies typed contracts, immutable hashes, exact UTC/as-of rules,
current/historical/future boundaries, supersession, conflict integrity,
freshness, evidence statuses, bounded fallback, authority/isolation
negatives, and the Step 22 boundary.

## Controlled validation

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python3 scripts/run_step21_temporal_resolution_validation.py
```

Expected final JSON field: `"status":"PASS"`. The runner verifies committed
Step 16 and Step 20 evidence digests before producing the following cases:

- `CURRENT_APPLICABLE`;
- `HISTORICAL_AS_OF`;
- `FUTURE_NOT_YET_EFFECTIVE`;
- `SUPERSEDED`;
- `CONFLICTING`;
- `STALE`;
- `INSUFFICIENT`;
- `UNAVAILABLE`;
- `COMPLETENESS_FALLBACK`.

Real repository identities are used for integration. Because the verified
local fixture is not a proven multi-version temporal family, temporal edge
cases are explicitly synthetic. A missing real multi-version fixture is not
reported as a real conflict.

## Required regressions

Run the existing Step 15-20, German Law temporal/HAT, authority, tenant, and
serialization suites in addition to full discovery. No Step 21 change may
weaken an earlier route, retrieval, source-authority, or scope boundary.

## Static boundaries

```bash
rg -n \
  "openai|anthropic|gemini|provider_call|model_call|chat_completion|generate_text|DraftV1" \
  src/aioa_memory_kernel/temporal \
  src/aioa_memory_kernel/german_law/temporal_resolution.py \
  2>/dev/null || true
```

Docstrings that state a prohibited later feature is absent are acceptable.
Functional provider/model, answer, approval, execution, or external-action
paths are forbidden.

## Failure semantics and cleanup

Route/bundle/hash/scope mismatches raise a sanitized fail-closed Step 21
boundary error. Invalid temporal facts return `INVALID`; unresolved conflict
returns `CONFLICTING`; missing coverage remains explicit.

The validator starts no process, database, or temporary runtime, writes no
source or external-volume artifact, and performs no cleanup beyond normal
process exit. Step 16 source data and Step 20 evidence remain read-only.

Step 22: NOT STARTED.
