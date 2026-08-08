# Step 17 routing and policy validation 1A

## Preconditions

Run from the canonical repository on `main`. The accepted Step 17 baseline is
`c51b32373dba5437f027268d1806a3fcdc1b3a91`. The worktree must contain only
the intended Step 17 changes during implementation and must be clean after the
closure commit.

The validation is local, deterministic, non-mutating, and requires no AWS,
network, provider, database, credential, corpus download, or external volume.

## Focused validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest tests.test_step17_routing_policy -q

PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/run_step17_routing_policy_validation.py
```

The script must report `status=PASS` and contain all four Axis A decisions,
all three knowledge-policy decisions, all four execution-authorization
decisions, and `answer_evidence_separation.status=PASS`.

## Regression validation

```bash
PYTHONPYCACHEPREFIX=/tmp/memory-patch-step17-pycache \
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/validate_contracts.py
```

Also run the existing Step 10, Step 16, HAT registry, German Law HAT,
authority, tenant, and contract serialization suites. No existing test may be
weakened.

## Expected decision matrix

| Trusted input | Axis A result |
| --- | --- |
| No eligible candidate | `PASS_THROUGH` |
| One eligible advisory HAT | `HAT_ASSIST` |
| One eligible mandatory HAT | `HAT_ENFORCE` |
| Multiple unresolved mandatory HATs | `AMBIGUOUS` |

| Policy/evidence condition | Knowledge result |
| --- | --- |
| Allowed scope and sufficient complete evidence | `ALLOW_ANSWER` |
| Out of scope, policy denial, or unavailable evidence | `BLOCK_ANSWER` |
| Human-review ceiling, partial, or conflicting evidence | `REQUIRE_CONFIRMATION` |

Execution ceilings are independently reproduced as `ALLOW`, `ALLOW_SCOPED`,
`REQUIRE_HUMAN`, and `DENY`. None is executed by this validator.

## Static boundary checks

```bash
rg -n \
  "requests|urllib|httpx|socket|subprocess|Popen|os\\.system|shell=True|boto|aws|psycopg|provider_call|model_call" \
  src/aioa_memory_kernel/routing || true

rg -n \
  "vector|rerank|hybrid|full.?text|tsvector|inverted.?index|exact_retriev|metadata_retriev|retrieval_sql" \
  src/aioa_memory_kernel/routing tests/test_step17_routing_policy.py || true
```

Every effect-check hit requires review. Documentation references are not
runtime effects. No retrieval implementation is permitted.

## Failure semantics

Unknown, disabled, untrusted, mismatched, out-of-scope, cross-tenant,
cross-user, hash-mismatched, or conflicting inputs fail closed. A mandatory
candidate failure cannot become `PASS_THROUGH`; it becomes `AMBIGUOUS`.
Partial evidence requires confirmation, while empty insufficient evidence
blocks the answer. Model/provider assertions do not change any decision.

The committed evidence is
`docs/evidence/routing/step17-routing-policy-validation.json`.
