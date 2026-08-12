# Step 41 Full Security and Regression Validation 1A

Status: controlled validation complete in the pre-commit closure worktree from Step 40 base
`b6248056ecf7563e8352425afe8fa59022a09938`.

This runbook executes the repository engineering security campaign. It does
not claim an external penetration test, vulnerability certification, or
production authorization.

The observed result is `PASS_FULL_SECURITY_REGRESSION_CONTROLLED`, with
2167/2167 final tests and 1485/1485 focused security tests passing. The
canonical evidence digest is
`f095c4cb42ece2d1ef156b8a19233927c3bfd85c7a58feb31aae91629d0b32a7`.

## Safety preflight

1. Require `main`, a clean worktree, the expected remote, local HEAD equal to
   `origin/main`, divergence `0 0`, and no active Git operation.
2. Require Step 40 COMPLETE AND PUSHED, Step 41 NOT STARTED, and Step 42 NOT
   STARTED.
3. Record the exact pushed Step 40 base SHA.
4. Read `AGENTS.md`, the roadmap, Step 40 profile/evidence/closure, and the
   Step 41 threat matrix before changing code.
5. Check only whether the approved provider capability is present. Never print
   or copy its value. Do not read secret stores or credential files.
6. Use only owned disposable CockroachDB runtimes. Run database-heavy
   validators sequentially on the constrained host.

No production AWS, S3, database, source-publication, secret-rotation, or other
external mutation is authorized.

## Focused campaign

Use the repository virtual environment and keep bytecode out of the tree:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python \
  -m unittest tests.test_step41_security_campaign -q
```

Then run the focused families for contracts, tenant boundaries, RLS,
source/retrieval authority, temporal handling, correction and verification,
Personal Memory Steps 27-32, audit, review, UI, credential separation, failure
recovery, German Law, Critic optionality, and the 4 GB profile. Exact commands
and counts belong in closure evidence, not in a pre-validation claim.

## Authentication and browser negatives

The campaign must prove:

- exact OIDC public-origin callback binding;
- strict configured Host enforcement without attacker-controlled redirect;
- state/nonce/PKCE/signature/issuer/audience and expiry checks;
- exactly one callback `code` and exactly one callback `state` parameter;
- streamed 256 KiB maximum strict OIDC discovery/token/key-set JSON, with
  duplicate keys, malformed UTF-8, and non-finite numbers rejected;
- single-use pending state and session rotation;
- opaque secure server-session cookies and server-side logout invalidation;
- POST-only same-session CSRF validation;
- local bounded return paths with encoded and plain traversal rejected;
- streamed 32 KiB URL-encoded mutation bodies, no multipart upload, no
  duplicate fields, valid UTF-8, bounded field count, control-character
  rejection, and bounded state versions;
- Jinja autoescape, strict CSP, and HTMX evaluation/script-tag processing
  disabled;
- zero owner or tenant IDOR success;
- SQL-shaped values remain query parameters and never SQL structure.

## Disposable CockroachDB campaigns

Run sequentially and retain only sanitized JSON output in temporary mode-0600
captures:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python \
  scripts/run_step36_credential_authority_validation.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python \
  scripts/run_step37_failure_recovery_validation.py
```

Step 36 must report role/grant and FORCE RLS success, zero leakage, no broad
runtime BYPASSRLS, no Commit Helper approval, and complete owned cleanup. Step
37 must report a complete recovery matrix, no authority widening, no duplicate
semantic effect, no false-success audit, and complete cleanup.
Its live SQLSTATE probes reuse the owned pgwire/SQL-API transport and must not
spawn an additional CockroachDB CLI process on the constrained host.

## German Law live replay

The Step 38 runner deliberately scans for and rejects any post-Step38
production bridge. That historical closure guard must not be weakened or
allowlisted during Step 41.

Therefore the bounded live provider plus same-database proof is replayed from
the exact canonical Step 38 closure commit
`939395d355ce0630c5044c4ab427082c3cf72d23` in a temporary detached Git
worktree. Current-tree Step 38/39/40 unit and controlled regressions are run
separately. This combination is classified exactly as:

`CANONICAL_STEP38_CLOSURE_REPLAY_PLUS_CURRENT_TREE_REGRESSION`.

The replay must use the already approved provider configuration, the minimum
bounded calls, one owned disposable CockroachDB runtime, and complete cleanup.
It must not overwrite historical Step 38 evidence.

## Current Critic and 4 GB regressions

Run:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python \
  scripts/run_step39_critic_bridge_validation.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python \
  scripts/run_step40_4gb_resource_validation.py
```

Step 39 remains provider-free for exhaustive Critic negatives. It must prove
disabled/enabled/failure core equality and zero Critic authority. Step 40 must
freshly measure the constrained profile, remain within its declared peak
budget, preserve audit/RLS/verifier/owner isolation, and clean up its embedding
measurement child.

## Static, dependency, and frontend checks

Run:

```text
.venv/bin/python -m pip check
npm run check:assets
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python \
  -m compileall -q src scripts tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python \
  scripts/validate_contracts.py
git diff --check
```

Review tracked production source for shell execution, unsafe deserialization,
untrusted model code, wildcard CORS, production debug, insecure TLS, hardcoded
credentials, unsafe temporary-file use, SQL interpolation, and unbounded
runtime queues. Fixed constant SQL projection fragments and the validation-only
Cockroach CLI queue must be classified explicitly rather than reported as
user-controlled production injection.

The current approved environment has no `pip-audit`, Safety, Bandit, Semgrep,
or JavaScript lock file. `pip check`, exact pinned UI dependencies, repository
tests, and static inspection are executed. Evidence must say that no external
vulnerability-database scan was performed; it must not fabricate one.

## Full regression and evidence

Run the full suite after every bounded fix:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python \
  -m unittest discover -s tests -p 'test*.py' -q
```

After all captures and counts are final, invoke
`scripts/run_step41_full_security_regression_validation.py` with the five
sanitized result captures, exact focused/full counts and durations, an
offset-aware validation timestamp, and `--write-evidence`.

The aggregator verifies every upstream canonical digest, all zero authority
and leakage counters, disposable cleanup, Step 38 live model/coherent lineage,
Step 39 optionality, Step 40 budget, base reachability, and `git diff --check`.
It atomically writes only:

`docs/evidence/security/step41-full-security-regression-validation.json`.

## Closure and cleanup

Before commit, require zero unexpected security skip, zero leak/escalation/
unauthorized-success counter, complete live cleanup, no temporary database or
model artifacts in Git, no Step 42 implementation, and only Step 41 changes.

Create one commit with subject:

`test(security): complete full security regression 1a`

Push without force, fetch, and require HEAD equal to `origin/main`, divergence
`0 0`, and a clean worktree. Step 42 remains NOT STARTED.
