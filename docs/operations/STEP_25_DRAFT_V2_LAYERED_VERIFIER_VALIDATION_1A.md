# Step 25 Draft V2 Layered Verifier Validation 1A

## Repository and upstream preflight

```bash
git status -sb
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count main...origin/main
git log -1 --format='%H%n%s' origin/main
```

Require clean `main`, local/origin equality, Step 24 complete and pushed, and
Steps 25 and 26 not started before implementation. Verify that the Step 24
architecture, ADR-031, runbook, validation evidence, closure record, roadmap,
and AGENTS checkpoint are reachable from `origin/main`.

## Packet integrity preflight

The caller must supply the exact `DraftV1`, `CorrectionPacketV1A`, and
`CorrectionPacketIntegrityReceipt`, plus a Kernel-side authenticator for the
receipt key ID. Step 25 verifies packet/receipt hashes, HMAC-SHA-256, Draft V1,
request, tenant, user, route, query, and Step 21 lineage before constructing a
provider request.

Production key provisioning remains outside the repository. Unit and
controlled validation instantiate the public non-production test vector
`bytes(range(32))`; it is test data, not a production credential. Never place
production key material in commands, logs, committed evidence, provider
payloads, or model context.

## Focused and full tests

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest tests.test_step25_draft_v2_layered_verifier -q

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 -m compileall -q src scripts tests
python3 scripts/validate_contracts.py
```

The focused suite covers packet/HMAC tamper rejection before model calls,
exact provider projection, retry bounds, no-open-transaction enforcement,
stage-2 replay, exact spans, correction and prohibition compliance,
fact/date/source/temporal checks, citation and evidence binding, semantic
candidate signals, deterministic-failure precedence, all summary states,
tenant/user isolation, model non-authority, and the Step 26 boundary.

## Controlled offline validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/run_step25_draft_v2_verification_validation.py
```

Expected top-level status is `PASS`. The runner first verifies the committed
Step 24 validation digest. It then creates bounded typed Steps 20-24 fixtures
and proves:

- Draft V2 generation from Draft V1 plus the verified packet only;
- one provider call and zero provider calls on exact replay;
- required correction application and prohibited-claim absence;
- valid and invalid citation behavior;
- deterministic date failure overriding semantic support;
- semantic support and uncertainty behavior;
- `VERIFIED`, `INCOMPLETE`, `FAILED`, and `CONFLICTING` summaries;
- packet/HMAC tamper rejection;
- zero retrieval, web, AWS, S3, approval, execution, and memory effects.

The script prints canonical JSON. Compare it with
`docs/evidence/modeling/step25-draft-v2-layered-verifier-validation.json` and
verify the `validation_digest` using the repository canonical hash helper.

## Provider validation

Ordinary tests and the controlled validator use deterministic fake generation
and verifier adapters. They do not read provider credentials and do not use
network access.

The canonical provider remains the Step 22 Moonshot
`moonshot-v1-8k` declared-version model. A real Step 25 call is optional under
the established Step 22 closure: the prior paid endpoint was quota-bound and
real-provider factual quality is not the Step 25 authority boundary. If an
operator separately performs a real validation, they must use only the
approved adapter/model, runtime-injected credentials, the same tool-less
settings, bounded timeout/retry, non-sensitive input, and no open database
transaction. Do not substitute another provider or claim success when the
approved runtime is unavailable.

## Static boundaries

```bash
rg -n \
  "VerifiedAnswer|FinalAnswer|final_answer|human_review|retry_once|personal_memory.*proposal|commit_helper" \
  src/aioa_memory_kernel \
  tests/test_step25_draft_v2_layered_verifier.py || true

rg -n \
  "subprocess|os\.system|shell=True|boto|aws|s3|approval|commit_helper|personal_memory.*write" \
  src/aioa_memory_kernel/modeling \
  src/aioa_memory_kernel/corrections \
  src/aioa_memory_kernel/verification || true
```

Review all hits. Historical docs, closed reason names, and explicit boundary
assertions are allowed; functional Step 26, external-action, retrieval, shell,
AWS/S3, approval, or memory-write capability is forbidden.

## Expected failures and cleanup

Packet hash, receipt, HMAC, key ID, Draft V1, route, tenant/user, citation,
date, source-authority, or scope detachment fails closed. Transient provider
errors may retry once; authentication, invalid request, policy, identity, and
response-contract failures do not. Semantic timeout/malformed output remains
unverified and never passes automatically.

The controlled path starts no external process, database, port, or model
runtime and makes no network call. Cleanup is `NOT_REQUIRED`. The test store
is process-local. No AWS or S3 object is read or mutated.

Step 26: NOT STARTED.
