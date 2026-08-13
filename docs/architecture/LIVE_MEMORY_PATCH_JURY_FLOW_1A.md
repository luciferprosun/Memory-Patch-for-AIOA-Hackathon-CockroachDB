# Live Memory Patch Jury Flow 1A

## Outcome

D3 binds the current Memory Patch path to the existing unified
FastAPI/Jinja2/HTMX cockpit. The current route remains the default and the
Critical Prompt Loop remains a separate `DISABLED_WITH_ARCHIVAL_VIEW` origin
surface. D3 adds no second application, authentication boundary, provider
client, persistence abstraction, migration, or legacy authority bridge.

The canonical ASGI target remains
`aioa_memory_kernel.demo_runtime.asgi:app`. The authenticated browser surface
is `GET /memory/demo`; an explicit CSRF-bound `POST /memory/jury-runs` creates
one guided run, and owner/session-authorized
`GET /memory/jury-runs/{run_id}` returns a bounded HTMX projection. Page load,
mode selection, and status polling make zero provider calls.

## Guided cases

D3 exposes only the two existing Step 38 cases from
`tests/fixtures/step38_german_law_cases.json`:

| Case | Question SHA-256 | Behavior |
|---|---|---|
| `primary-entry-into-force` | `f33243aa0b47a12cf7e86bae77c079d20d573f43ccea740c0dafc91d738dfa0b` | Primary date case; a correct Draft V1 is reported without manufacturing a defect |
| `backup-special-case-reservation` | `b1e31110b94216f9caf8e46dce800328f7f9a00de614935199c12acb876755da` | Declared special-case fallback; only its exact allowed response shapes continue |

There is no arbitrary legal-question endpoint in D3. The page calls these
guided demonstration cases, not general legal advice. The exact questions and
hashes are rendered before submission.

## One current application service

`LiveMemoryPatchJuryFlow` is the application boundary. It composes the
existing current contracts rather than implementing their logic in the web
layer:

1. trusted German Law HAT registration, route, and policy gate;
2. exact published-statute retrieval through the normal RLS-bound application
   transaction runner;
3. Step 20 Evidence Bundle assembly;
4. Step 21 temporal, conflict, and freshness resolution;
5. Step 22 Draft V1 generation through `GuardedProviderAdapter`;
6. Step 23 claim/evidence binding;
7. Step 24 Correction Packet construction;
8. Step 25 evidence-bound Draft V2 generation and layered verification;
9. Step 26 fail-closed Verified Answer finalization.

The current Step 22 request contract is bound to the resolved request lineage,
so route/retrieval/temporal preflight occurs before the paid Draft V1 call.
`prove_draft_v1_evidence_blind` then verifies that only the original question
crosses that model-input boundary: evidence, the correct fact, the Correction
Packet, Personal Memory, and legacy output are absent. The browser trace keeps
the jury narrative in Question, Draft V1, Evidence, Correction, Draft V2 order
while reporting only stages that actually completed.

Every paid call is made within one server-derived `ProviderRequestScope` and
through the existing `GuardedProviderAdapter` and durable
`CockroachProviderGuardLedger`. Provider identity remains
`openrouter / moonshotai/kimi-k2`; tools, web, code, fallback, and substitution
remain disabled. D3 adds no browser provider surface.

## Typed progressive projection

`BoundedJuryRunCoordinator` owns only a short-lived presentation projection:

- one worker;
- at most 20 retained runs;
- 30-minute terminal TTL;
- a 14-stage maximum;
- 8 evidence items and 16 correction items maximum;
- 16 KiB maximum per stage/answer text;
- exact tenant, owner, and opaque-session-digest binding;
- server-side idempotency binding for duplicate submit;
- deterministic shutdown and owned-resource cleanup.

The projection contains a run ID, guided-case identity, current state, safe
reason code, bounded summaries, and artifact hashes. It does not copy provider
credentials, database credentials, OIDC tokens, unrelated Personal Memory, or
unbounded evidence. Canonical evidence, verification, audit, and Personal
Memory remain in their existing repositories.

This projection is intentionally per-process and ephemeral. A process restart
may discard the jury trace and require a new guarded run; it cannot discard or
alter canonical evidence or Personal Memory. The canonical one-worker 4 GB
profile prevents restart from creating a competing in-process ledger or
multi-worker projection. No migration is required.

## Fourteen truthful stages

The UI projection is fixed to:

1. User Question
2. Draft V1
3. Route / HAT Decision
4. Retrieved Evidence
5. Temporal / Conflict / Freshness
6. Claim Analysis
7. Correction Packet
8. Draft V2
9. Layered Verification
10. Verified Answer
11. Personal Memory Proposal
12. Owner Approval
13. Commit / Activation
14. Later Question / Reuse

Each stage is `NOT RUN`, `RUNNING`, `COMPLETED`, `BLOCKED`, or
`NOT APPLICABLE`. A correct Draft V1 yields `CORRECTION_NOT_REQUIRED`; D3 does
not fabricate a Correction Packet defect, Draft V2, Verified Answer, or memory
proposal. A verifier failure emits no Draft V1 fallback. Only the Step 26
result can populate the Verified Answer card.

## Personal Memory boundary

D3 does not automatically create a Personal Memory proposal from a live run.
A verified correction is marked eligible for the existing Step 28-29 path,
without claiming that the path ran. The cockpit independently projects only
real owner-scoped records returned by the existing UI backend:

- real `AWAITING_APPROVAL` proposals use the existing POST route;
- approval requires CSRF, exact proposal hash, exact state hash/version, an
  explicit unchecked confirmation, and server-derived owner identity;
- approval alone is not displayed as commit or activation;
- an `ACTIVE` patch is displayed only when its real patch/activation state is
  present;
- later reuse is described as owner-scoped context availability with its
  actual model binding and patch hash;
- canonical evidence authority remains false and canonical evidence wins.

The D3 flow owns no candidate, approval, Commit Helper, activation,
publication, reviewer, or Personal Memory mutation service. Legacy mode has no
Personal Memory path at all.

## Authority and security

| Capability | Current D3 flow | Legacy archival view |
|---|---:|---:|
| Read canonical published evidence | Through current RLS-bound service | No |
| Guarded provider call | Guided current run only | No |
| Emit Verified Answer | Step 25/26 only | No |
| Write canonical evidence or route policy | No | No |
| Approve Personal Memory | Existing explicit owner route only | No |
| Commit or activate Personal Memory | Existing separated authority only | No |
| Browser credential or owner authority | No | No |

All routes reuse OIDC Authorization Code + PKCE, deny-by-default judge access,
durable server-side sessions, Secure/HttpOnly/SameSite cookies, CSRF/origin
validation, CSP, and security headers. Tenant and owner values submitted by a
browser are ignored; every run is bound to the trusted principal and current
session. Jinja autoescaping treats questions, model text, evidence, correction
text, and Personal Memory as untrusted. No `safe` template filter, unsafe
`innerHTML`, inline script, or browser-side provider request is introduced.

## Local and hosted parity

Local rehearsal and D4 hosting use the same ASGI app, routes, templates,
database adapter, provider guard, and auth/session system. Only typed runtime
configuration changes. The canonical launcher remains:

```bash
.venv/bin/python scripts/run_demo_runtime_1a.py serve
```

The controlled D3 campaign uses a deterministic fake provider and controlled
retrieval adapter behind the real Step 17-26 and provider-guard contracts. It
makes zero paid calls. The prior R6/R7 real-provider proof remains the live
lineage reference; D5 owns the next hosted Golden Path proof.

Operational validation is documented in
[`D3_LIVE_MEMORY_PATCH_JURY_FLOW_VALIDATION_1A.md`](../operations/D3_LIVE_MEMORY_PATCH_JURY_FLOW_VALIDATION_1A.md).
