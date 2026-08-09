# Step 26 Verified Answer Validation 1A

## Repository preflight

```bash
git status -sb
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count main...origin/main
git log -1 --format='%H%n%s' origin/main
```

Require clean `main`, local/origin equality, Step 25 complete and pushed, and
Steps 26 and 27 not started before implementation. Verify the Step 25
architecture, ADR-032, runbook, evidence, closure, roadmap, and AGENTS
checkpoint from the exact base.

## Final input integrity

Before output, verify the Step 17 route and final policy result; every primary
or fallback Step 20 outcome/bundle; Step 21 temporal result and evidence
status; Draft V1; Step 24 packet and receipt; and the complete Step 25 Draft
V2 pipeline result. The Kernel-side authenticator must verify the receipt HMAC
and key ID. Never put HMAC key material in logs, evidence, model input, or
committed files.

## Focused and full validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest tests.test_step26_verified_answer_output -q

PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p 'test*.py' -q

python3 -m compileall -q src scripts tests
python3 scripts/validate_contracts.py
```

The focused suite covers exact answer text/hash, route/policy/evidence
ceilings, citations and claim coverage, tenant/user/HAT isolation, packet and
summary tampering, `HAT_ENFORCE` no-fallback behavior, one retry, distinct
retry identity, full re-verification, review, bounded failures, transaction
separation, and Step 27/approval/execution negatives.

## Controlled validation

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 scripts/run_step26_verified_answer_validation.py
```

Expected top-level status is `PASS`. The runner verifies the committed Step 25
evidence digest, uses real typed Step 20-25 fixture contracts, and covers:

- verified answer success with exact Draft V2 bytes;
- `BLOCK_ANSWER` and `REQUIRE_CONFIRMATION`;
- insufficient, conflicting, and stale evidence;
- HAT_ENFORCE failure without Draft V1 fallback;
- exactly one successful retry and one exhausted retry fixture;
- full retry re-verification and unchanged packet/evidence identity;
- review and bounded-failure hashes;
- citation and verification-summary tamper negatives;
- zero retrieval, web, AWS/S3, Personal Memory, approval, and execution effects.

Compare canonical output with
`docs/evidence/modeling/step26-verified-answer-fail-closed-validation.json` and
verify its `validation_digest` using the repository canonical hash helper.

## Retry validation

Retry is allowed only with `SUFFICIENT` evidence and a correctable Step 25
compliance/generation failure. It uses the existing Step 22 text provider,
tools disabled, one provider attempt, the same Correction Packet, no new
evidence, and no open persistence transaction. The complete Step 25 verifier
must run over the new Draft V2. Confirm the retry Draft hash differs from the
original and the provider call count never exceeds one.

Ordinary and controlled validation use deterministic fakes. The approved
Moonshot provider remains available only through the Step 22 adapter. A real
retry call is not required under the established quota-bound Step 22
acceptance and must not be fabricated. If separately run, use only the pinned
adapter/model, runtime-injected credentials, non-sensitive content, bounded
timeouts, and no open transaction.

## Static boundaries

```bash
rg -n \
  "return .*draft_v1|fallback.*draft_v1|draft_v1.*fallback|known.?bad.*draft" \
  src/aioa_memory_kernel/answers src/aioa_memory_kernel/modeling || true

rg -n \
  "PersonalMemory|personal_memory.*slot|patch_proposal|memory_activation|quota.*personal|model_binding.*personal" \
  src/aioa_memory_kernel/answers tests/test_step26_verified_answer_output.py || true

rg -n \
  "subprocess|os\.system|shell=True|approval|commit_helper|execute_action|external_action|control_write" \
  src/aioa_memory_kernel/answers || true
```

Review hits that state the invariant or future boundary. There must be no path
that returns Draft V1 after verification failure and no Personal Memory,
approval, commit, shell, or external-action capability.

## Failure semantics and cleanup

Integrity, policy, evidence, and non-retryable verification failures do not
call a provider. Retry failure produces review with no second retry. Failure
messages are bounded and exclude provider bodies, stack traces, credentials,
database details, and machine-local paths.

The controlled runner creates no process, port, database, model runtime, or
temporary external store. Cleanup is `NOT_REQUIRED`. Step 27 remains NOT
STARTED.
