# Step 22 Model Adapter and Draft V1 Validation 1A

## Preflight

```bash
git status -sb
git branch --show-current
git rev-parse HEAD
git rev-list --left-right --count main...origin/main
```

Require clean `main`, exact equality with `origin/main`, Step 21 complete, and
Step 22/23 not started before implementation.

## Provider identity and credential

The approved config is
`config/modeling/moonshot-v1-8k-step22-1a.json`. For an optional controlled
live call, set `MOONSHOT_API_KEY` in the local environment. Never write the
value into the repository, shell history, logs, test output, or evidence.

The validator checks the exact model ID, owner, and context window against the
provider's live model registry. A missing credential or unavailable provider
is reported explicitly and never triggers a fallback vendor/model.

## Focused and full validation

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python3 -m unittest tests.test_step22_model_adapter_draft_v1 -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 -m compileall -q src scripts tests
python3 scripts/validate_contracts.py
```

The focused suite captures the exact provider call and proves that the
original query is present while the unique correction-evidence sentinel,
Step 20 bundle, Step 21 assessments, authority metadata, and correction hints
are absent.

## Controlled provider validation

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python3 scripts/run_step22_model_draft_v1_validation.py
```

Expected top-level status is `PASS`. The deterministic fake-provider matrix is
always required. If the approved credential and provider capacity are
available, one bounded real text generation is also attempted. If provider
capacity is unavailable, the real case reports `UNAVAILABLE` with a sanitized
reason while the validator still proves the live model-registry identity. It
does not substitute another model or fabricate a response.

## Timeout, retry, and persistence expectations

- attempt timeout: 45 seconds;
- maximum attempts: 2;
- retry only transient classes;
- no model call inside a DB transaction;
- `memory_patch.drafts`, `draft_stage=1`, no new migration;
- exact replay performs no second provider call;
- conflicts fail closed;
- existing tenant/user RLS and FORCE RLS remain active.

## Static capability checks

```bash
rg -n \
  "subprocess|os\\.system|shell=True|boto|aws|s3|psycopg|cockroach|aioa_memory_kernel\\.persistence|git|approval|commit_helper|personal_memory.*write" \
  src/aioa_memory_kernel/modeling/providers || true

rg -n \
  "EvidenceBundle|evidence_bundle|temporal_resolution|conflict_group|freshness|source_authority|required_correction|prohibited_claim" \
  src/aioa_memory_kernel/modeling || true
```

The orchestration service may import the transaction-boundary assertion; the
provider-specific adapter may not import persistence. Step 21 identity may
appear in out-of-band models but never in prompt projection.

## Failure and cleanup

Failures are stable reason codes. Provider bodies, secrets, and raw headers
are not emitted. The validator starts no database or temporary runtime, makes
no AWS/S3 mutation, and leaves no owned process or port to clean up.

Step 23: NOT STARTED.
