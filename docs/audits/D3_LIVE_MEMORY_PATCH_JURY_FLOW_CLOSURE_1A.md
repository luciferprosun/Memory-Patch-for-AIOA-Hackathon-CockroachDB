# D3 Live Memory Patch Jury Flow Closure 1A

## Outcome

`D3 COMPLETE AND PUSHED - READY FOR D4` is the required post-push verdict.
This closure record is bound to the Git commit containing its bytes; the exact
SHA and remote synchronization result are reported after the commit is
created, avoiding a self-referential commit hash.

D3 starts from D2 commit
`83202538749ff9e02b96719638eb3ccb8ed3daaf`. It keeps Memory Patch as the
default current production-authority mode and preserves Critical Prompt Loop
as the separate D2 `DISABLED_WITH_ARCHIVAL_VIEW` origin surface. D4, AWS,
public deployment, DNS, and Step 44 were not started.

The architecture is documented in
[`LIVE_MEMORY_PATCH_JURY_FLOW_1A.md`](../architecture/LIVE_MEMORY_PATCH_JURY_FLOW_1A.md),
the click path in
[`D3_MEMORY_PATCH_JURY_CLICK_PATH_1A.md`](../demo/D3_MEMORY_PATCH_JURY_CLICK_PATH_1A.md),
and the machine-readable result in
[`d3-live-memory-patch-jury-flow-1a.json`](../evidence/demo/d3-live-memory-patch-jury-flow-1a.json).

## Unified current flow

The existing ASGI app now exposes one authenticated current-mode path:

- `GET /memory/demo` renders the cockpit and performs no provider call;
- `POST /memory/jury-runs` validates the existing CSRF token and submits one
  approved guided case;
- `GET /memory/jury-runs/{run_id}` returns an owner-and-session-bound HTMX
  projection and performs no provider call.

`LiveMemoryPatchJuryFlow` composes the existing Step 17-26 route, retrieval,
Evidence Bundle, temporal, Draft V1, claim binding, Correction Packet, Draft
V2, layered verification, and Verified Answer services. Route, retrieval, and
temporal preflight provide the current Step 22 request lineage, but
`prove_draft_v1_evidence_blind` proves that only the original question crosses
the Draft V1 provider boundary. The current provider remains OpenRouter with
`moonshotai/kimi-k2`, behind `GuardedProviderAdapter` and the durable
`CockroachProviderGuardLedger`.

The current page exposes exactly the canonical primary
`primary-entry-into-force` and backup
`backup-special-case-reservation` cases. A correct primary result is shown as
`CORRECTION_NOT_REQUIRED`; no defect, Draft V2, Verified Answer, or memory
proposal is manufactured. A blocked verifier produces no Draft V1 fallback.
Only a successful Step 25 and Step 26 result populates the Verified Answer
projection.

## Progressive and bounded projection

The 14-stage projection is limited to one worker, 20 retained runs, a
30-minute terminal TTL, 8 evidence items, 16 correction items, and bounded
text. The opaque run reference is bound to the server-derived tenant, owner,
and current session digest. Duplicate submits with the same server-issued
idempotency identity return the existing run. Polling stops in the template
when the run becomes `COMPLETED` or `BLOCKED`.

The projection is deliberately short-lived and per process. It does not
duplicate canonical business state and requires no migration. A restart may
discard only the presentation trace; it cannot alter canonical evidence,
provider accounting, or Personal Memory.

## Personal Memory and legacy boundaries

D3 creates no automatic Personal Memory proposal. A verified correction is
only marked eligible for the existing Step 28-29 path. The cockpit separately
projects real owner-scoped Personal Memory state through the existing backend:

- a real `AWAITING_APPROVAL` proposal uses the existing CSRF-bound approval
  route and begins unchecked;
- the action binds the existing proposal, evidence-validation, and state
  identities;
- approval does not claim commit or activation;
- only a real `ACTIVE` patch displays its hash, model binding, and later-use
  availability;
- private memory remains non-canonical, and canonical evidence wins.

The D3 flow owns no Personal Memory mutation, Commit Helper, publication,
reviewer, or legacy service. The legacy view cannot enter the current trace,
call the provider, write evidence, change route/HAT, approve, commit, activate,
or write Personal Memory.

## Security result

Both views retain the one OIDC Authorization Code + PKCE boundary,
deny-by-default judge policy, durable server-side session, Secure/HttpOnly/
SameSite cookie policy, CSRF/origin validation, CSP, RLS/FORCE RLS, and
purpose-specific credentials. Browser-submitted tenant and owner identities
are not accepted as authority. Cross-owner and cross-session run access is
denied.

Jinja autoescaping remains active for questions, model output, evidence,
corrections, and memory text. There is no unsafe template filter,
`innerHTML`, browser provider request, raw traceback, or privileged secret in
the changed UI surface. Secret leakage, authority violations, and provider
guard bypasses are all zero.

## Validation

| Gate | Result |
|---|---|
| D3 focused UI, orchestration, authority, XSS, cases, and Step 17-26 controlled pipeline | `10/10 PASS` |
| D1-D3, R4-R7, Step35/36/38/39/43 focused regression | `347/347 PASS`, skipped `0` |
| Full repository unit-test discovery | `2337/2337 PASS` |
| D3 provider-free validator | `PASS`; digest `31db8dd5bc57a313de5b0263bf5b273346e4f8cb8f107a8140774f167c6f715b` |
| Contract validator | `PASS`; 5 schemas, 4 fixtures, 2 unrelated HAT manifests |
| Step35 UI asset checker | `PASS`; SHA-256 `22283ef68cb7545914f0a88a1bdedc7256a703d1d580c1d255217d0a50d31313` |
| Python compilation and `git diff --check` | `PASS` |
| Actual paid provider calls in D3 validation | `0` |
| Secret leakage / authority violations | `0 / 0` |
| AWS or public resources touched | `0` |

The controlled Step 17-26 pipeline uses a deterministic fake provider behind
the real guarded adapter and is labelled `DETERMINISTIC_TEST`. The production
composition defaults to `LIVE`. D3 does not repeat the prior bounded R6/R7
paid-provider proof; D5 owns the hosted proof.

## Git closure and handoff

The intended commit subject is
`feat(demo): add live Memory Patch jury flow 1a`. The exact commit SHA and push
result are reported after creation. The next activity after a successful push
is `D4 - AWS DEPLOYMENT ARCHITECTURE FREEZE + RUNTIME DEPLOYMENT 1A`.
