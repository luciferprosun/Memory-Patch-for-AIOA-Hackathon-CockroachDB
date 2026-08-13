# Post-Roadmap Review Gate 1A

## Verdict

`BLOCKED — FIX REQUIRED`

The frozen product, jury archive, Golden Path, owner UI security boundary and
4 GB resource profile remain coherent. The repository is not yet safe to
hand directly to a demo host, however, because it does not contain one
repository-native runtime assembly that turns the existing dependency-injected
components into a deployable process. In particular, no canonical ASGI
entrypoint/start command binds the real UI backend, OIDC client/settings,
purpose-specific CockroachDB connections and durable session store, and no
HTTP liveness/readiness routes expose the already implemented typed health
projection. Public access and provider-spend controls are documented as
choices, not frozen as an enforceable demo configuration.

Adding a plausible-looking `uvicorn` command without those dependencies would
misrepresent deployment readiness. This gate therefore records the blocker
without changing executable code, schema, authority, security policy, provider
identity or the closed roadmap.

## Repository guard

- Repository: canonical Memory Patch CockroachDB repository.
- Branch: `main`.
- Post-roadmap base SHA:
  `abd5b23860fcf39ab9671eb56fbbac2d45efa992`.
- Base subject: `docs(hackathon): add CockroachDB jury report archive 1a`.
- Starting `HEAD` and `origin/main`: exact equality.
- Starting divergence: `0 0`.
- Starting worktree: clean.
- Active merge, rebase, cherry-pick, revert or bisect: none.
- Step 43: `COMPLETE AND PUSHED` in the canonical roadmap.
- Step 44: absent and not started.

## Unresolved review item

The single entry in
[unresolved-review-items.json](../../Raporty%20Hackathon%20CockroachDB/manifest/unresolved-review-items.json)
is `prompt-files-not-materialized-for-most-roadmap-steps`.

- Exact missing artifact: original exact-byte, unversioned chat prompt files
  for most roadmap steps. There is no source file to hash or copy.
- Origin: interactive chat context outside Git; no source repository, commit or
  artifact SHA can be proven.
- Reason for review: the bounded housekeeping search found exact prompt files
  for the roadmap, Step 10 approval and Step 13, while closure commits and
  evidence prove execution of the remaining steps without preserving their
  original chat bytes as files.
- Classification: `ACCEPTED_KNOWN_LIMITATION`.
- Impact: archival completeness only. It does not affect product correctness,
  security, privacy, source authority, evidence integrity, Golden Path
  execution, deployment behavior, licensing or reproducibility of the tracked
  code and tests.
- Resolution: preserve the explicit gap and do not fabricate or reconstruct
  historical prompt bytes.

This is non-blocking. The jury archive was intentionally left byte-for-byte
unchanged so its closed manifest did not churn for a cosmetic reclassification.

## Jury archive integrity

The archive entry points remain judge-readable and correctly distinguish the
Memory Patch repository from the separate AOIA-Core foundation:

- [jury README](../../Raporty%20Hackathon%20CockroachDB/README.md);
- [master index](../../Raporty%20Hackathon%20CockroachDB/INDEX.md);
- [timeline](../../Raporty%20Hackathon%20CockroachDB/TIMELINE.md).

Revalidation results:

| Check | Result |
|---|---|
| Curated artifacts | `302/302` present |
| Unique artifact IDs | `302/302` |
| Unique artifact targets | `302/302` |
| Artifact SHA-256 and sizes | `302/302` exact |
| Manifest SHA-256 | `860a9e9991366e49946a9d01fea75c99ea258a3452a752ad39dd507143559c38`, exact |
| Archive-local links | `205` checked, `0` broken |
| Repository Markdown links | `733` checked across `225` documents, `0` broken |
| Archive symlinks/path escapes | `0` |
| AOIA-Core source artifacts | `42/42` source blobs and archived targets reverified read-only at exact commits |

AOIA-Core was fetched by exact commit into a disposable read-only verification
repository. It was not mutated or merged. Its entries remain identified as a
separate prior/cross-repository foundation rather than CockroachDB-repository
work.

## Secret, privacy and repository hygiene

A bounded scan covered all `766` baseline tracked files plus both new review
records. It checked provider, AWS,
database, OIDC/session/signing, bearer-token and private-key shapes, credentialed
database locators, tracked environment files, binaries, model/cache extensions,
large files and user-machine path shapes.

- Actual secret leakage: `0`.
- Private irrelevant data: `0`.
- Unexplained binary/model/cache artifacts: `0`.
- Tracked files above 1 MB: `0`.
- Tracked NUL/binary files: `0`.
- Tracked environment files: only `tooling/versions.env`, containing version
  and platform pins rather than credentials.
- Controlled negative/placeholder matches: `44`; these are test fixtures or
  explicit documentation placeholders.
- One additional scanner match was the literal credential-URL regular
  expression in the Step 36 scan runbook, not a credential value.
- Machine-path-shaped references were confined to intentional provenance,
  historical fixture, operations or negative-test material; no unrelated
  private content was copied.

Transient Python bytecode generated during inspection was removed from the
exact generated directories and was never tracked.

## Golden Path readiness

The canonical [Step 43 Golden Path](../demo/MEMORY_PATCH_DEMO_GOLDEN_PATH_1A.md)
remains truthful and complete.

- Primary case: `primary-entry-into-force`.
- Backup case: `backup-special-case-reservation`.
- Supported/no-unnecessary-correction case:
  `supported-entry-into-force-clean`.
- Fail-closed cases: `temporal-unavailable-edge` and
  `conflicting-ceiling-edge`.
- Step 43 replay: `PASS_DOCUMENTATION_DEMO_SUBMISSION_REPLAY`, zero network
  calls and zero database processes.
- Live lineage: the frozen Step 42 evidence retains
  `PASS_REAL_VERIFIED_LINEAGE` for OpenRouter model
  `moonshotai/kimi-k2`; it was not re-spent during this bounded gate.
- Primary flow: evidence-blind Draft V1 -> trusted routing/retrieval ->
  Evidence Bundle -> temporal/conflict handling -> claim binding -> Correction
  Packet -> Draft V2 -> layered verification -> Verified Answer.
- Personal Memory: explicit owner approval -> technical commit -> ACTIVE
  private patch -> later/cross-model retrieval, with
  `canonical_evidence_authority=false`.
- Critic: disabled by default, optional and candidate-only, with no evidence,
  approval, commit or activation authority.
- Replay is explicitly labelled as replay rather than a new live model call.

No fake retrieval, fake live-provider claim, Personal Memory authority claim,
Critic authority claim or verifier bypass was found.

## UI demo readiness

Classification: `UI_READY_WITH_DOCUMENTED_LIMITATION`.

The approved FastAPI/Jinja2/HTMX owner workspace exposes authenticated slots,
quota and model-binding state, proposals awaiting approval, explicit
receipt-driven owner approval, active/inactive patch history, revoke/export/
logical-delete actions and a safe owner audit projection. It does not expose a
public Commit Helper or activation route and does not require manual database
editing for the owner actions it implements.

The focused UI suites passed `25/25` tests, including OIDC Authorization Code
with PKCE, exact callback/state/nonce validation, secure opaque cookies,
server-derived owner identity, CSRF/origin/body bounds, XSS escaping,
cross-owner IDOR denial, stale-write conflict behavior, safe errors and zero
browser credential/database authority. The Step 36 credential/redaction suites
passed a further `26/26` tests.

Non-blocking UI limitation: the 12-stage German Law correction trace is
currently presented by the canonical CLI/replay and documentation rather than
an integrated browser page. The existing UI is the Personal Memory owner
workspace. A later presentation task may expose the already trusted server
projection, but must not invent state or move authority into the browser.

## Deployment preflight

Classification: `DEPLOYMENT_BLOCKED_MISSING_RUNTIME_ASSEMBLY`.

Available and validated building blocks:

- one-worker `memory-patch-4gb-demo-1a` profile, digest
  `eb205843f34039418bd49663ab47390777cbdc861f671172c8f025d588a55ad8`;
- typed startup order, 15-second readiness bound and 30-second shutdown bound;
- typed cheap health projection and fail-closed required-component semantics;
- exact 18-migration CockroachDB v26.2.4 chain and offline validator;
- purpose-specific credential inventory with no admin/master fallback;
- FastAPI application factory and tested OIDC/session/browser boundary;
- approved OpenRouter adapter `openrouter-chat-completions-step38-1a`, model
  `moonshotai/kimi-k2`, config digest
  `52e163ebef09076c135bc7c0783917bc1515666456253a2a62b4a8822630e15e`;
- hosted generation, one lazy process-level
  `intfloat/multilingual-e5-small` runtime, Critic off and ingestion off by
  default;
- audit, verifier, RLS/FORCE RLS, source authority and Commit Helper separation
  remain mandatory.

Blocking deployment gaps:

1. No canonical ASGI module/object and exact start/stop command assembles the
   application factory with real repository adapters.
2. No deployment assembly binds the application, migration, Commit Helper,
   audit/review and optional publication/ingestion database purposes to their
   exact credentials while preserving pool limits and TLS-verified CockroachDB
   connectivity.
3. No HTTP liveness/readiness endpoints expose the existing typed health
   result for an orchestrator or load balancer.
4. The default in-memory owner session store is explicitly local/controlled;
   no durable server-side implementation has been selected and injected for a
   hosted demo.
5. OIDC issuer/client/public-origin settings, migration-before-readiness
   behavior, external-volume/E5 verification and shutdown ownership have not
   been assembled into one fail-closed deployment configuration.
6. Judge-only authentication, request/body limits, rate limiting and a bounded
   provider-call/spend ceiling are recommendations in the runbook, not one
   enforceable public-demo profile.

These gaps are not evidence of a flaw in the frozen authority model, but they
prevent an honest claim that the repository can be deployed safely today.

## Required bounded repair

Perform one non-roadmap demo runtime-assembly task that:

1. adds a repository-native ASGI entrypoint and exact start/stop helper around
   the existing FastAPI factory;
2. wires only the existing purpose-specific CockroachDB, OIDC, audit,
   Personal Memory, provider and external-volume adapters, with missing inputs
   failing closed and TLS required;
3. selects a durable bounded server-side session store for hosted use;
4. exposes cheap HTTP liveness/readiness backed by the Step 40 health contract;
5. freezes judge-only access, bounded request/provider concurrency, rate and
   spend controls, with the provider credential server-side only;
6. documents exact environment names and migration/startup/shutdown order;
7. adds focused assembly/health/session/secret-boundary tests and reruns this
   review gate.

This repair must not introduce Step 44, change schema or provider/model
identity, redesign auth, weaken RLS/audit/verifier/approval boundaries or deploy
to AWS as part of the repair itself.

## Focused validation

| Validation | Result |
|---|---|
| Step 43 documentation/demo suite | `9/9 PASS` |
| Step 43 deterministic replay | `PASS_DOCUMENTATION_DEMO_SUBMISSION_REPLAY` |
| Step 35 UI and UI-security suites | `25/25 PASS` |
| Step 36 credential separation/redaction suites | `26/26 PASS` |
| Step 40 runtime/resource/optional suites | `37/37 PASS` |
| Step 41 security-campaign contract suite | `18/18 PASS` |
| Contract validator | `PASS`; 5 schemas, 4 fixtures, 2 unrelated HAT manifests |
| CockroachDB migration offline validation | `PASS`; 18 migrations, 43 schema tables, 40 protected tables |
| Python dependency consistency | `PASS`; no broken requirements |
| UI asset/security check | `PASS`; digest `22283ef68cb7545914f0a88a1bdedc7256a703d1d580c1d255217d0a50d31313` |
| Archive integrity/link/hash validation | `PASS` |
| Full repository Markdown links | `733/733 PASS` |

Only this audit and its machine-readable evidence are changed. Executable and
runtime paths changed: `0`.

## Evidence and commit binding

The machine-readable record is
[post-roadmap-review-gate-1a.json](../evidence/demo/post-roadmap-review-gate-1a.json).
It binds the exact post-housekeeping base and this review content. A Git commit
cannot embed its own SHA without a self-reference cycle; the exact audit commit
SHA is therefore recorded by Git and verified against `origin/main` after
push, while the evidence uses a symbolic containing-commit binding.
