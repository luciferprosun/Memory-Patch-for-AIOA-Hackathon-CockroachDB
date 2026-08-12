# Step 41 Full Security and Regression Closure 1A

Record status: PRE-COMMIT VALIDATION COMPLETE; READY FOR ONE CLOSURE COMMIT.

Step 40 base:
`b6248056ecf7563e8352425afe8fa59022a09938`

Final Step 41 Git commit: NOT CREATED.

Push to `origin/main`: NOT PERFORMED.

Step 42: NOT STARTED.

## Scope

Step 41 is a full repository security and regression closure campaign over the
system implemented through Step 40. It may make only bounded fixes for defects
that violate already-established contracts. It adds no product authority,
provider migration, AWS deployment, RC freeze, backup/restore implementation,
or Step 42 behavior.

The threat and runtime inventory is recorded in
`../security/STEP41_FULL_SECURITY_THREAT_MATRIX_1A.md`. The operating procedure
is `../operations/STEP_41_FULL_SECURITY_REGRESSION_VALIDATION_1A.md`.

## Baseline

- Branch and remote guard: PASS at task start.
- Local Step 40 base equals `origin/main`: PASS.
- Starting divergence: `0 0`.
- Starting worktree: clean.
- Pre-fix full regression: 2149/2149 PASS in 145.127 seconds.
- Step 41 and Step 42 were both NOT STARTED at the guard.

## Bounded defects discovered

The pre-fix UI probes found existing security-boundary defects. Closure is
permitted only after each fix and its adversarial regression remain green:

1. OIDC configuration accepted a redirect on an origin detached from the
   declared public origin and did not require the canonical callback path.
2. The owner UI accepted an arbitrary Host header.
3. OIDC return-path filtering used a prefix test that admitted `/memoryevil`
   and did not close encoded/traversal variants.
4. Mutation parsing materialized the full request body before enforcing the
   32 KiB bound and collapsed duplicate fields.
5. Control characters and unbounded numeric state versions could reach generic
   error handling instead of deterministic input rejection.
6. Vendored HTMX retained evaluation and injected-script processing defaults,
   although CSP already denied inline/eval script execution.
7. OIDC discovery, token, and key-set JSON responses were parsed without a
   pre-parse byte limit or duplicate-key rejection.
8. Step 37 live negative SQL probes spawned extra CockroachDB CLI processes.
   On the constrained host the extra client could fail to dial the healthy
   owned server after the migration campaign, masking the intended
   serialization-retry proof.
9. The OIDC callback relied on framework scalar query binding and did not
   explicitly reject duplicate `code` or `state` parameters.

The bounded fixes bind OIDC origin/callback and metadata URLs, install exact
trusted-host enforcement, canonicalize local return paths, stream and strictly
decode URL-encoded mutations, reject duplicates/multipart/invalid UTF-8/control
characters/unbounded state versions, and explicitly disable HTMX evaluation
and script tags. OIDC JSON is streamed under a 256 KiB limit and parsed with
duplicate-key, invalid UTF-8, non-finite-number, and malformed-shape rejection.
The callback now requires exactly one code and exactly one state value before
consuming or creating authentication state.
No route, evidence, Personal Memory, audit, credential, or authority semantics
changed.

The Step 37 validator now reuses its already-owned bounded pgwire/SQL-API
transport for the `40001`, exact-replay, and changed-replay probes. It retains
the exact SQLSTATE assertions while avoiding another CockroachDB process; no
production runtime or recovery policy changed.

## Validation ledger

| Gate | Required result | Observed result |
|---|---|---|
| Step 41 focused/adversarial | zero failure/error | 1485/1485 PASS in 112.236 s |
| Disposable Step 36 role/RLS | PASS and cleanup | PASS; digest `b2b7b433...d8f7de` |
| Disposable Step 37 recovery | PASS and cleanup | PASS, 55 cases; digest `11ca850e...7b9109` |
| Canonical Step 38 live replay | PASS live coherent lineage | PASS; digest `f4f75237...79f98b` |
| Current Step 39 Critic | PASS, optional, zero authority | PASS; digest `de40a26e...34665f0` |
| Fresh Step 40 4 GB profile | PASS within budget | PASS, 786/3000 MiB; digest `2b6a9303...cce3a3` |
| UI asset check | PASS | PASS |
| Python dependency consistency | PASS | PASS |
| External vulnerability DB scan | truthful limitation | NOT PERFORMED: tool/lock unavailable |
| Compileall | PASS | PASS |
| Contract validator | PASS | PASS |
| Full final regression | zero failure/error | 2167/2167 PASS in 130.592 s |
| `git diff --check` | PASS | PASS |

## Security acceptance counters

Every final counter below must be zero before the record can close:

- secret leakage;
- cross-tenant unauthorized success;
- cross-owner unauthorized success;
- IDOR success;
- SQL injection success;
- CSRF bypass success;
- XSS execution success;
- authority escalation success;
- Commit Helper approval bypass success;
- Critic authority escalation success;
- unauthorized canonical evidence inclusion;
- known-bad Draft V1 fail-open;
- undetected tested audit tamper;
- production resources touched.

Observed final values: every listed counter is exactly `0`.

## Known limitations retained

- This is a repository engineering campaign, not an external penetration-test
  or certification.
- No approved vulnerability-database scanner is installed and the UI has no
  JavaScript dependency lock. This campaign does not claim that such a scan
  occurred.
- The pre-existing Starlette/httpx test-client deprecation warning remains; it
  is not a security-test failure.
- The Step 38 live runner intentionally rejects later-step source. Live Step 38
  is therefore replayed at its canonical closure commit, while the current tree
  receives full Step 38-40 regression coverage. Its historical scanner and
  evidence are not weakened or rewritten.

## Evidence and verdict

Evidence file:
`../evidence/security/step41-full-security-regression-validation.json`

Evidence digest:
`f095c4cb42ece2d1ef156b8a19233927c3bfd85c7a58feb31aae91629d0b32a7`.

Final regression: 2167/2167 PASS in 130.592 seconds.

Closure verdict: PASS in the pre-commit worktree. All live, focused,
full-regression, evidence, scope, secret, and cleanup gates are green. The
final Git commit remains `NOT CREATED` and push remains `NOT PERFORMED` until
the one authorized closure commit is created and made reachable from
`origin/main`.

Step 42 remains NOT STARTED and is not authorized by this draft.
