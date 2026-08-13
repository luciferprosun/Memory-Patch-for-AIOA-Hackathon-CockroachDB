# Post-Roadmap Demo Runtime Closure 1A

## Outcome

`READY FOR DEMO DEPLOYMENT`

R2-R7 resolves the post-roadmap review finding
`DEPLOYMENT_BLOCKED_MISSING_RUNTIME_ASSEMBLY` without creating Step 44 or
changing the Memory Patch authority model. The repository now has one
import-safe ASGI target, one controlled launcher, purpose-separated
CockroachDB startup, durable owner sessions, deny-by-default judge access,
one guarded hosted-model boundary, and deployment-compatible liveness and
readiness endpoints.

This record is the human-readable companion to
[`post-roadmap-runtime-closure-1a.json`](../evidence/demo/post-roadmap-runtime-closure-1a.json).
The cumulative runtime is bound to the pre-runtime base
`d74a9b15cd3d60812bc3055e03985930c38e1bcd` and to the Git commit containing
these files. The containing commit is the non-self-referential final identity;
the commit SHA is reported after push rather than embedded into its own bytes.

## Final runtime

- ASGI target: `aioa_memory_kernel.demo_runtime.asgi:app`.
- Application factory: the existing Step 35
  `create_personal_memory_app`; no competing FastAPI tree was added.
- Launcher: `.venv/bin/python scripts/run_demo_runtime_1a.py serve`, with
  explicit `check-config` and `prepare-database` preflights.
- Profile: `memory-patch-4gb-demo-1a`, one web worker.
- Database: CockroachDB v26.2.4 through
  `CockroachRuntimeDatabaseFactory`, `PsycopgMigrationSqlClient`,
  `CanonicalMigrationCoordinator`, and `PsycopgApplicationPool`.
- Migrations: the single canonical 19-file manifest, latest migration
  `0019_post_roadmap_demo_runtime_state`; migrations close before the normal
  application pool opens.
- Authentication: the existing `HttpxOidcClient` Authorization Code + PKCE
  flow and deny-by-default `JudgeAccessPolicy`.
- Sessions: durable bounded `CockroachOwnerSessionStore`, with opaque handles,
  expiry, rotation, revocation, owner binding, RLS and FORCE RLS.
- Provider: existing `OpenRouterDraftV1Adapter`, frozen to `openrouter` /
  `moonshotai/kimi-k2`, wrapped by `GuardedProviderAdapter` and the durable
  `CockroachProviderGuardLedger`.
- Personal Memory: existing `PersonalMemoryService`,
  `PersonalMemoryApprovalService`, `PersonalMemoryLifecycle32Service`, and
  `KernelPersonalMemoryUiBackend`; approval and Commit Helper boundaries are
  unchanged.
- Health: `GET /health/live` is a minimal process probe; `GET /health/ready`
  returns 200 only after the complete mandatory runtime dependency set is
  safe, otherwise 503 with a bounded reason.

Startup validates typed configuration and purpose-specific credentials,
enforces hosted CockroachDB `sslmode=verify-full`, closes canonical migrations,
opens the bounded normal-role pool, initializes durable sessions and judge
auth, loads the approved provider identity without a completion, initializes
the durable call guard, and only then becomes ready. Shutdown marks readiness
false before refusing and draining owned work, closing provider/OIDC/session/
database resources, and removing only owned temporary resources.

## Authority and public-demo safety

The final gate revalidated these non-negotiable answers:

| Question | Answer |
|---|---|
| Can an unauthenticated browser trigger a paid provider call? | `NO` |
| Can an authenticated but disallowed judge trigger a paid provider call? | `NO` |
| Can browser code read the provider credential? | `NO` |
| Can provider code read database credentials? | `NO` |
| Can provider code approve, commit or activate Personal Memory? | `NO` |
| Can a client spoof owner or tenant authority? | `NO` |
| Can the app use migration/admin fallback? | `NO` |
| Can readiness become true before mandatory dependencies are initialized? | `NO` |
| Can budget exhaustion start a paid call? | `NO` |
| Can a failed verifier return Draft V1 as the final answer? | `NO` |
| Can Critic become canonical-evidence authority? | `NO` |

The browser has zero privileged secrets and zero database authority. The
provider has zero database, Commit Helper, reviewer, source-publication or
approval authority. The normal application principal remains distinct from
the migrator and Commit Helper; reviewer and audit-reader authority remain
separate. Personal Memory remains private and non-canonical, and canonical
evidence wins on conflict. RLS and FORCE RLS remain mandatory. There is no
provider/model or admin/master fallback.

## Golden Path and cost boundary

R7 reuses rather than re-spends the R6 live proof in
[`post-roadmap-r6-golden-path-runtime-proof-1a.json`](../evidence/demo/post-roadmap-r6-golden-path-runtime-proof-1a.json).
The approved real provider ran `primary-entry-into-force`; its evidence-blind
Draft V1 contained a genuine material defect. The unchanged output continued
through canonical German Law routing, exact/full-text/vector/hybrid retrieval,
temporal resolution, claim binding, Correction Packet, Draft V2, layered
verification, and a hash-bound Verified Answer. The backup case was not
needed. The explicit owner action then exercised the private Personal Memory
proposal, approval, Commit Helper, ACTIVE retrieval and compatible cross-model
reuse path without promoting private memory to canonical evidence.

The R5/R6 accounting contract is a **CALL-COUNT CEILING**, not an exact billed
currency claim. The hard epoch ceiling is 32 calls, with 12 per owner, 10 per
session, eight per request, one concurrent call, two queued callers, a
two-second queue wait, 24,576 input bytes, 1,024 output tokens and a 45-second
provider timeout. CockroachDB stores the reservations so a process restart
does not reset the armed epoch. Unknown completion remains charged
conservatively. Critic is disabled by default and cannot bypass accounting.
R7 made zero additional paid provider calls.

## Validation

| Gate | Result |
|---|---|
| Full repository regression | `2304/2304 PASS` in 146.651 seconds |
| Focused assembled-runtime security campaign | `253/253 PASS` |
| R6 deterministic and live campaign | `404/404 PASS`; live lineage `PASS_REAL_VERIFIED_LINEAGE` |
| Canonical contract validator | `PASS`; 5 schemas, 4 fixtures, 2 unrelated HAT manifests |
| Step 35 UI asset/security validator | `PASS`; SHA-256 `22283ef68cb7545914f0a88a1bdedc7256a703d1d580c1d255217d0a50d31313` |
| Python dependency consistency | `PASS`; external vulnerability database scan unavailable in the approved local toolchain |
| Python compilation | `PASS` |
| Repository Markdown links | `736/736 PASS` |
| Jury archive | `302/302` hashes, 302 unique IDs, 205/205 links, no path or symlink escape |
| Jury manifest SHA-256 | `43286ca960ce95819946a76f668937bcc14213fd6aa012caf2bba2a047911684` |
| Step 40 constrained profile | `PASS_4GB_CONTROLLED`; 784 MiB conservative core peak against 3,000 MiB configured peak |
| Current-schema native backup/restore | `PASS`; native jobs succeeded, 19/19 replay, synthetic session 1 -> 1, RLS/FORCE RLS and security catalog preserved |
| Secret/private/binary/cache hygiene | `PASS`; zero unexplained findings |
| `git diff --check` | `PASS` |

The full regression initially exposed two failures and two errors in historical
Step 42/43 assertions: the checks incorrectly rebuilt the immutable Step 42 RC
manifest from the new post-roadmap tree. The bounded R7 fix preserves the exact
Step 43 baseline runner in the jury archive, validates the historical RC against
its own canonical bytes and digest, and explicitly reports current post-roadmap
runtime drift. It does not alter the frozen evidence or weaken an acceptance
gate. Focused Step 42/43 validation passed `14/14` after the correction.

## Recovery and RC invariants

Step 42 remains the immutable release-candidate backup/restore proof. The
post-roadmap runtime intentionally adds migration 0019, so R7 does not pretend
the current tree has the old RC content digest. It preserves the frozen
manifest and exact Step 43 runner while creating independent current-runtime
closure evidence. The controlled R7 recovery check uses the pinned v26.2.4
binary, all 19 current migrations, a synthetic non-private durable session,
native CockroachDB backup and an isolated restore. It validates replay,
session-row reconciliation, and RLS/FORCE RLS before exact cleanup.

The source applied and replayed all 19 migrations before the native backup.
The isolated target received only the exact canonical cluster-role prerequisite
blocks from migrations 0004, 0014, 0017 and 0018, then restored the database
with grants. All 19 ledger entries replayed as skips; the synthetic session
count reconciled `1 -> 1`; `owner_ui_sessions` retained RLS and FORCE RLS; and
the complete Step 36 security catalog assertion passed. The exact target
process exited, all owned ports closed, both disposable recovery roots were
removed, and no production resource was touched.

Production resources touched: zero. Public deployment mutations: zero. The R6
runtime and R7 disposable recovery resources leave zero owned orphan processes
and zero owned temporary runtimes.

## Known limitations and deployment boundary

The accepted proof is a loopback hosted-style single-process demo, not a public
AWS deployment or production HA/DR certification. R6 used the explicit local
disposable CockroachDB TLS exception and a controlled server-derived auth
harness; the deployment operator must supply real verified database TLS,
HTTPS termination, the external OIDC issuer/client/callback, and the judge
allowlist. The Step 40 memory number is the existing controlled profile
measurement, not a new R7 production SLA. The external vulnerability database
scanner was not installed; pinned dependency consistency and repository-native
security tests passed. Multi-worker distributed rate-window coordination is
outside the one-worker demo contract, while the database-backed hard provider
call ceiling is restart-safe.

Deployment must not proceed if `check-config` or `prepare-database` fails,
`/health/ready` is not 200, verified TLS/HTTPS or the exact OIDC callback is
unavailable, judge access is not deny-by-default, test auth or in-memory
sessions are enabled, the provider/model identity differs, the provider budget
is not explicitly armed, a privileged value could reach browser code, or the
one-worker 4 GB bounds cannot be honored.

## Git closure and handoff

The intended single commit subject is
`feat(runtime): assemble bounded jury demo runtime 1a`. After validation it is
pushed to `origin/main` and verified by exact HEAD equality, divergence `0 0`,
and a clean worktree. The commit SHA and remote result are reported in the R7
operator report because a Git commit cannot safely contain its own identity.

The post-roadmap runtime mini-roadmap ends at R7. The exact next activity is
`DEMO UI POLISH + DEPLOYMENT 1A`; it is not Step 44 and is not executed here.
