# Step 39 Critic Bridge Validation 1A

## Purpose and current status

This runbook will validate the optional AOIA Critic Prompt Loop bridge from
the exact Step 38 closure commit
`939395d355ce0630c5044c4ab427082c3cf72d23`.

Step 39 implementation and controlled validation are complete in the closure
worktree. The canonical result is `PASS_PROVIDER_FREE_CONTROLLED`, validation
digest
`de40a26eadf342b04d7b7b7ff10cc4c2b9c95322c4b37fc54a5af6b5d34665f0`.
The final report records the one closure commit and push observation. Step 40
is `NOT STARTED`.

Mandatory validation is offline and provider-free. It uses the real frozen
Step 38 hash lineage with a deterministic synthetic Critic adapter. It must
not be reported as a real Critic or real provider run.

## Repository guard

From the repository root inspect:

```text
git status -sb
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git remote -v
git diff --name-only
git rev-list --left-right --count main...origin/main
```

Before implementation require clean `main`, expected remote, no active Git
operation, local/origin equality, and exact Step 38 base
`939395d355ce0630c5044c4ab427082c3cf72d23`. Before closure, allow only files
directly attributable to Step 39 and require Step 40 to remain unmodified.

Read all applicable `AGENTS.md` files and the full production roadmap. Do not
update roadmap or `AGENTS.md`, commit, or push until every Step 39 closure gate
passes.

## Dependency preflight

Verify, without mutating a database, that the repository exposes:

- the Step 22 provider-neutral text-generation boundary and approved provider
  identity contract;
- Step 23 claim and evidence relations;
- Step 24 Correction Packet identities;
- Step 26 Verified Answer identities;
- Step 27 owner-private slot and model-binding contracts;
- Step 28 `CRITIC_PROMPT_LOOP` source, trigger, envelope, intake service,
  idempotency, quota, RLS, and FORCE RLS contracts;
- Step 29 independent evidence validation and proposal deduplication;
- Step 30 human approval, Commit Helper, and activation separation; and
- Step 33 audit event, actor, subject, redaction, and hash-chain contracts.

No schema migration is expected for Step 39. If implementation requires one,
stop and reconcile that scope expansion before continuing.

## Frozen German Law fixture

The provider-free lane must read the already-committed Step 38 evidence and
fixture; it must not rewrite either artifact.

| Check | Exact value |
| --- | --- |
| Step 38 evidence file SHA-256 | `b43152c0b7e9020b4078f2abd89dc25986b14cbd09e5cf4b7a67ce525130eb13` |
| Step 38 validation digest | `b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042` |
| fixture file SHA-256 | `03afd8caa0a37bb911cfd968a157305d939caff0cbd7275e690ae669ac9af342` |
| golden suite | `b9c424e2b00ed13317f73d4fd86bbd2c96b1f2e606765f98dc35cadc0663cd50` |
| selected case | `primary-entry-into-force` |
| verified upstream lineage | `b3175f1b3476aa88453bca4623475c0ce7835488af623f51a43b0b7b8a793a23` |
| route | `e6b1912195e8c3cecb2a79cab235592c21b9fe6cab0ce5c0fa8c2e7a18794d0c` |
| Draft V1 | `17513ff270ad13cab959bec4e80856cf3f9e1b69658b2c0040d3023cefba5139` |
| claim snapshot | `ba7e1e3cb6dcdfbf54f2373cfc03a327416a799a48fb809fa4c9e05f7697b148` |
| evidence bundle | `563401ac507d0eef448b0b189bbfde3a8ab117c17eedc490ba92a2d977ed291e` |
| temporal result | `1b60e3fd725debaedf5e6f62cd41ba700f44a59d2b0239b1756112fa1db5e116` |
| Correction Packet | `5af8baa1a3708dc2fd6e6f951392b7799ef85a7a2065c91e5b01367e02b26bb6` |
| Verified Answer | `21b3e8fd4f9c38eddcb5a545fe7d6b1631310357d3260ea3b31015fe0168cdea` |

Classify the test fixture exactly as
`REAL_STEP38_HASH_LINEAGE + SYNTHETIC_CRITIC_ADAPTER`. Do not copy the Step 38
Kernel candidate, proposal, approval, commit, activation, or active-patch hash
into a Step 39 result.

## Static and focused validation

Once implementation and tests exist, run:

```text
python3 -m compileall -q src scripts tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.test_step39_critic_bridge -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest tests.test_step39_critic_authority -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python \
  -m unittest tests.test_step39_critic_german_law_e2e -q

python3 scripts/validate_contracts.py
```

The focused suite must prove:

- immutable canonical request, prompt, assessment, receipt, result, mapping,
  and hash reconstruction;
- exact shared request/run/route/HAT/scope/artifact/claim/evidence lineage;
- request byte bounds and closed JSON output with duplicate/unknown-key,
  trailing-text, Unicode, type, count, and size negatives;
- disabled mode makes zero provider and Step 28 calls;
- enabled issue mode calls the fake provider once and maps only through the
  Step 28 Critic intake;
- no-issue mode makes no candidate;
- unavailable, timeout, malformed, detached, or authority-claiming output
  fails closed only for the Critic path;
- exact replay is idempotent and changed content conflicts;
- cross-user, cross-tenant, wrong slot, wrong route, wrong scope, unknown
  claim/evidence, changed prompt/provider, and stale binding are denied;
- an equivalent Kernel and Critic correction cannot become duplicate active
  memory after Step 29 deduplication;
- the existing candidate audit event is hash-only; and
- all approval, commit, activation, reviewer, source, route, execution, and
  external-action authority values remain false.

## Controlled provider-free validation

Run the checked-in orchestrator after it exists:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  scripts/run_step39_critic_bridge_validation.py
```

The orchestrator must not read `OPENROUTER_API_KEY`, call a provider, start a
database, mutate AWS/S3, or perform an external action. It uses deterministic
fake ports and in-memory/fake Step 28 intake sufficient to prove the bridge
contract. It must emit one sanitized result and no raw question, draft,
evidence snippet, correction text, prompt, response, credential, or personal
memory content.

Required controlled cases:

1. `CRITIC_DISABLED`: zero provider and candidate calls, core hashes unchanged.
2. `CRITIC_ENABLED_ISSUE`: one exact fake response, accepted assessment,
   trusted-scope mapping, and one `DETECTED` Step 28 candidate.
3. `CRITIC_ENABLED_NO_ISSUE`: accepted no-issue assessment and no candidate.
4. `CRITIC_PROVIDER_UNAVAILABLE`: bounded failure receipt and no candidate.
5. `CRITIC_INVALID_OUTPUT`: closed parser/integrity rejection and no candidate.
6. `CRITIC_EXACT_REPLAY`: same result/receipt identity and no duplicate effect.
7. `CRITIC_CHANGED_REPLAY`: conflict and no changed candidate.
8. `CRITIC_OWNER_TENANT_DENIAL`: no cross-user or cross-tenant target.
9. `CRITIC_DUPLICATE_CORRECTION`: Step 29 prevents duplicate memory content.
10. `CRITIC_AUDIT_AUTHORITY`: one accepted-candidate hash-only audit draft and
    zero later-step authority.

## Full regression and security scans

After focused validation succeeds, run:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  -m unittest discover -s tests -p 'test*.py' -q

python3 scripts/run_cockroachdb_migrations.py --offline-validate
python3 scripts/validate_contracts.py
git diff --check
```

Also inspect the Step 39 changes for provider credentials, raw prompt/output,
broad database roles, direct proposal/commit/activation calls, execution
capabilities, and Step 40 implementation. Historical Step 38 boundary evidence
must remain unchanged; it described the Step 38 closure before Step 39 existed
and must not be rewritten to make a post-Step39 scan pass.

## Audit gate

For an accepted durable candidate require:

```text
event_type=CORRECTION_CANDIDATE_DETECTED
actor_type=CRITIC_LOOP
subject_type=CORRECTION_CANDIDATE
redaction_profile=HASH_ONLY
```

Verify request, prompt, provider receipt, assessment, mapping, candidate,
route, run, and scope hash lineage. Disabled, no-issue, provider failure,
parser rejection, and integrity rejection must not be represented as durable
candidate events. Do not invent a Step 33 event type to make the matrix look
complete.

## Sanitized evidence gate

The final controlled output is expected at:

```text
docs/evidence/critic/step39-critic-prompt-loop-bridge-validation.json
```

It must record the exact Step 38 base, fixture classification, prompt/policy
digests, request/result/assessment/receipt/mapping/candidate/audit hashes,
disabled/no-issue/failure/tamper matrices, Step 28-only proof, Step 29/30 gate
proof, owner/tenant denials, zero secrets, zero authority escalation, cleanup,
and a canonical validation digest.

The checked-in artifact is the exact canonical controlled-runner output. Its
digest is independently reconstructed by the focused E2E test, and the entire
object passes the shared secret and machine-path rejection policy.

## Changeset and closure gate

Before staging inspect:

```text
git status --short
git diff --check
git diff --stat
git diff --name-only
```

Require all focused and full tests, controlled validation, contract validator,
authority scans, evidence verification, and documentation checks to pass with
zero failures. Only then update roadmap and `AGENTS.md`, stage only Step 39,
create one closure commit, push `main`, fetch, and verify clean `0 0`
divergence.

All non-Git closure gates in this section pass. The one-commit push workflow
performs and reports the remaining Git observations. Step 40 remains
`NOT STARTED`.

## Expected failure states

- Missing Step 38 hashes: `BLOCKED`, no Critic request.
- Critic disabled: expected closed result, core continues.
- Provider unavailable or retries exhausted: Critic-only failure, core
  continues.
- Invalid or oversized output: rejected before mapping.
- Unknown claim/evidence or changed scope: integrity rejection.
- Missing owner-private target: diagnostic-only, no persistence.
- Step 28 owner, slot, quota, idempotency, or RLS denial: no new candidate.
- Step 29 evidence or duplicate denial: no proposal progression.
- Any approval/commit/activation or execution authority: validation failure.
- Any Step 40 implementation: scope violation.

## Cleanup

The mandatory controlled lane starts no database process and performs no
network call, so no runtime resource should remain. Remove only runner-owned
temporary files, verify no credential was retained, and leave repository data
and frozen Step 38 evidence unchanged.
