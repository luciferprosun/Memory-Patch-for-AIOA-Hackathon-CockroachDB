# Step 23 Claim and Evidence Binding Validation 1A

## Preflight

```bash
git status -sb
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count main...origin/main
```

Require clean `main`, exact equality with `origin/main`, Step 22 complete, and
Step 23/24 not started before implementation.

## Focused and full validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest tests.test_step23_claim_evidence_binding -q

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 -m compileall -q src scripts tests
python3 scripts/validate_contracts.py
```

The focused suite proves exact spans, deterministic IDs and hashes,
support/refute/unverified candidate semantics, stale/future/conflict ceilings,
authority and tenant/user/HAT binding, snapshot immutability, and the absence
of Step 24 contracts.

## Controlled offline validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/run_step23_claim_evidence_validation.py
```

Expected top-level status is `PASS`. The validator binds the committed Step 22
validation identity and exercises the real typed Step 20, Step 21, Step 22,
and Step 23 contracts with a bounded deterministic fixture. The committed Step
22 evidence does not retain the real hosted provider response text, so it is
reported as hash-only real lineage; support, refutation, temporal mismatch,
and conflict edges are explicitly synthetic.

Expected main matrix:

- `SUPPORTED=1`;
- `REFUTED=1`;
- `UNVERIFIED=4`;
- one non-factual segment;
- one compound claim;
- material conflict and future-effective negatives pass.

## Static boundary checks

```bash
rg -n \
  "CorrectionPacket|required_corrections|prohibited_claims|packet_hmac|DraftV2|draft_v2|final_verifier" \
  src/aioa_memory_kernel/claims tests/test_step23_claim_evidence_binding.py || true

rg -n \
  "requests|httpx|urllib|socket|subprocess|boto|aws|s3|psycopg|persistence" \
  src/aioa_memory_kernel/claims || true
```

Hits that merely document the Step 24 boundary are allowed. Functional Step
24, network, provider, database, AWS/S3, approval, or execution paths are not.

## Failure semantics and cleanup

Input hash/binding mismatch, invalid span, weak authority, wrong publication
state, or scope mismatch fails closed with a stable Step 23 reason. The
validator starts no database, process, port, or temporary runtime; cleanup is
`NOT_REQUIRED`. It performs no network, provider, AWS, or S3 operation.

Step 24: NOT STARTED.
