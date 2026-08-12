# Step 43 Demo and Submission Runbook 1A

## Scope

This runbook presents the already frozen Step 42 RC. It does not migrate the
database, change provider/model identity, alter Personal Memory state-machine
semantics, grant Critic authority, deploy production infrastructure, or expose
credentials. The demo topology is constrained and non-HA.

## Preflight

From the repository root:

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count HEAD...origin/main
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python scripts/validate_contracts.py
npm run check:assets
```

Require `main`, clean worktree, equal local/remote SHAs, divergence `0 0`, and
successful validators. For the pre-closure Step 43 implementation tree, the
frozen base is `f99057c601bfa41115185f52141ea327f3ef1aa1`; after closure, verify
that this base is an ancestor of the pushed Step 43 commit.

Verify the Step 42 RC manifest, Step 40 4 GB profile, German Law fixture, and
local E5 artifact through the linked [submission index](../SUBMISSION_INDEX_1A.md).
Do not print environment values. A provider credential is reported only as
present/absent.

## Deterministic replay — default

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python docs/demo/run_step43_demo.py --mode replay --pretty
```

Expected status:

```text
PASS_DOCUMENTATION_DEMO_SUBMISSION_REPLAY
```

Replay performs zero network calls and starts zero database processes. It
recomputes the Step 38 validation/trace hashes, Steps 39–42 evidence digests,
Step 41/42 zero security counters, submission artifact digest, and Markdown
links. It is labeled `REPLAY_NOT_A_NEW_LIVE_PROVIDER_VALIDATION`.

## Cost-bounded live observation

Use only when the exact approved provider credential is available server-side
and the operator intentionally accepts the cost:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python docs/demo/run_step43_demo.py \
  --mode live --allow-live-provider-cost --pretty
```

The command:

- consumes only the pinned OpenRouter/Kimi provider credential;
- permits at most two evidence-blind calls (primary, then backup only when
  needed), each with at most 96 output tokens and a 45-second timeout;
- performs no automatic retry or provider fan-out;
- keeps tools, functions, web, code, DB, and external actions unavailable;
- prints no credential;
- never writes live model text into committed evidence;
- truthfully falls back to the already verified RC replay when no selectable
  live defect is observed.

This short live observation is not a replacement for the complete real
provider/retrieval/correction/Personal Memory proof in Step 42.

## 4 GB demo shape

Use profile `memory-patch-4gb-demo-1a` when starting the full UI/runtime:

- one web/backend worker;
- hosted generation, no local generation model;
- one lazy process-level E5 runtime with bounded batch/thread settings;
- Critic disabled by default;
- ingestion disabled after the validated corpus is prepared;
- bounded DB pools, queues, results, and provider concurrency;
- external-volume model/cache reuse;
- audit, verifier, RLS, owner auth, and Commit Helper separation always on.

The canonical startup and readiness procedure remains in the [Step 40
runbook](STEP_40_4GB_RUNTIME_VALIDATION_1A.md). Do not make a public unmetered
generation endpoint. Use judge-only access, an explicit rate limit, or BYOK.

## Case selection

1. Run `primary-entry-into-force` once.
2. If the live Draft V1 contains the correct canonical date, run
   `backup-special-case-reservation` once.
3. If the backup is also correct or its exact shape is invalid, stop paid
   calls and use replay. Do not manufacture a defect.
4. Always include `supported-entry-into-force-clean` to show no unnecessary
   correction.
5. Include one temporal/conflict case to show `HUMAN_REVIEW_REQUIRED`.

## Personal Memory demonstration

Use only synthetic demo owner/tenant data. Show the candidate/proposal and
`AWAITING_APPROVAL` before any owner action. Authenticate as the owner, submit
the CSRF-protected approval, and wait for distinct server receipts before
showing `APPROVED`, `COMMITTED`, or `ACTIVE`. Do not expose Commit Helper or DB
credentials. Later retrieval must show the same active patch and
`canonical_evidence_authority=false`.

## Recording checklist

- [ ] RC/base SHA and pushed Step 43 SHA are understood and displayed safely.
- [ ] Working tree is clean and local/remote divergence is `0 0`.
- [ ] Required services are healthy; CockroachDB migrations are valid.
- [ ] German Law corpus fixture is available.
- [ ] Local E5 artifact identity is verified.
- [ ] Provider credential is present server-side when LIVE is selected.
- [ ] Provider credential is absent from browser/static output.
- [ ] Critic is disabled unless explicitly demonstrated.
- [ ] Ingestion is disabled unless the prepared corpus must be rebuilt.
- [ ] The 4 GB profile is selected for the full runtime demo.
- [ ] Primary and backup case behavior has been rehearsed truthfully.
- [ ] Replay artifacts are available and clearly labeled.
- [ ] Personal Memory uses a synthetic owner and prepared demo slot.
- [ ] No unrelated real user data is present.
- [ ] No secret is visible in terminal, UI, recording, or evidence.
- [ ] Cleanup procedure is understood.

## Validation before submission

```bash
python3 -m compileall -q src scripts tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest tests.test_step43_documentation_demo -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python docs/demo/run_step43_demo.py --mode replay
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest discover -s tests -p 'test*.py' -q
.venv/bin/python scripts/validate_contracts.py
.venv/bin/python -m pip check
npm run check:assets
git diff --check
```

## Cleanup

The Step 43 replay and live-observation commands start no local DB or child
service. For a full UI/runtime rehearsal, stop only the explicitly owned
processes using the Step 40/42 cleanup procedure. Confirm owned PIDs exited,
ports closed, temporary runtime stores were removed, and no provider key or
demo output was written to the repository. Never use a broad recursive delete
against the repository, home directory, or shared external-volume root.

## Troubleshooting

- Missing provider credential: use replay; never substitute a different
  provider or embed a key.
- Primary correct: switch once to the backup case.
- Both live cases correct/invalid: stop live calls and present verified replay.
- Broken digest/link: stop; repair the specific Step 43 presentation reference
  without changing frozen RC semantics.
- RC/security/reference mismatch: fail closed and do not submit the result as a
  passing demo.
