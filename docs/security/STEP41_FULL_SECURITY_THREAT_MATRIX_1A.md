# Step 41 Full Security Threat Matrix 1A

Status: validated in the Step 41 pre-commit closure worktree against Step 40 base
`b6248056ecf7563e8352425afe8fa59022a09938`. This is a repository engineering
threat model and regression inventory, not an external penetration-test or
security certification.

## Scope and trust boundaries

The campaign treats browser input, owner identifiers in URLs, source text,
retrieved text, model output, Critic output, review annotations, and imported
metadata as untrusted. Trusted authority is derived only from typed server-side
contracts, authenticated request context, CockroachDB roles and FORCE RLS,
published Source Registry lineage, verified evidence, explicit owner approval,
and narrow technical receipts.

The primary boundaries are:

1. browser to FastAPI owner workspace;
2. OIDC provider to the server-side authenticated owner session;
3. application services to CockroachDB through purpose-bound identities;
4. model-provider output to typed Draft and verification contracts;
5. source acquisition and retrieval to published canonical evidence;
6. Personal Memory candidate, proposal, approval, commit, activation, and
   retrieval stages;
7. Critic output to Step 28 candidate intake only;
8. reviewer decisions to typed, revalidated downstream handoff;
9. business events to the append-only audit hash chain;
10. the constrained runtime to its remote DB/provider and external-volume
    dependencies.

## Current runtime and authority inventory

### Identity and owner-facing access

- FastAPI, Jinja2, and vendored HTMX serve one server-rendered owner workspace.
- OIDC Authorization Code with S256 PKCE is server mediated. State and nonce
  are random, bounded, single-use values. ID-token signature, algorithm, key
  identity, issuer, audience, expiry, issued-at time, subject, nonce, tenant,
  and owner claims are verified before `OwnerPrincipal` construction.
- Discovery, token, and key-set JSON is streamed under a 256 KiB bound and
  rejects duplicate keys, malformed UTF-8, and non-finite JSON numbers.
- The configured redirect is bound to the exact public origin and
  `/memory/oidc/callback`. Trusted-host filtering rejects an unconfigured Host
  header without redirecting it. The callback requires exactly one `code` and
  one `state` query parameter.
- The browser receives opaque random cookie handles only. Server memory stores
  only hashes of those handles. Authentication rotates the session, expiry is
  bounded, and logout invalidates server-side state.
- Session cookies are `Secure`, `HttpOnly`, `SameSite=Lax`, and path bounded.
  Every mutation is POST-only and requires same-session CSRF validation.
- Tenant and owner identity come from verified server-side session state, not
  form fields, query strings, or path identifiers.

### Browser boundary

The browser package has no database, migration, Commit Helper, provider,
source-publication, reviewer-service, audit-writer, AWS, or S3 credential. It
has no direct commit, activation, generic state-setting, source-publication, or
external-action route. Jinja autoescape and a strict CSP protect rendered
untrusted text. HTMX evaluation and injected script processing are explicitly
disabled.

### Database identities and roles

The capability matrix separates normal application, source publication,
Personal Memory Commit Helper/activation, reviewer, review service, audit
reader/exporter, migrator, and S3 runtime capabilities. Ordinary application,
Commit Helper, reviewer, review service, audit reader, provider, and browser
paths have no broad BYPASSRLS capability. The operations-only migrator is the
intentional broad administrative identity and is not a runtime fallback.

Security-sensitive owner and tenant tables retain FORCE RLS. The live campaign
checks exact grants and negative cross-tenant, cross-owner, role escalation,
context spoof, source publication, audit mutation, review, Commit Helper, and
migration attempts against one disposable pinned CockroachDB runtime.

### Provider and model boundary

The approved generation path is the pinned OpenRouter adapter and
`moonshotai/kimi-k2` configuration. Provider and response identities, prompts,
generation parameters, request/response hashes, timeouts, and bounded retry
receipts are typed. Tools, web access, code execution, and arbitrary function
calling are disabled. The provider receives only its model-call capability and
has no database, evidence, approval, commit, activation, reviewer,
source-publication, audit mutation, AWS, S3, Git, or external-execution
authority.

### Source and evidence authority

German Law authority remains rooted in the Source Registry, published HAT
manifest/version lineage, access and publication filters, Step 17 route, Step
20 Evidence Bundle, Step 21 temporal/conflict/freshness result, Step 23 claim
binding, Step 24 Correction Packet, Step 25 layered verifier, and Step 26
Verified Answer eligibility. Vector prefixes and model confidence are never
authorization or evidence authority.

### Personal Memory authority

Step 27 owner slots, quotas, and model bindings precede Step 28 candidate
intake. Step 29 alone moves evidence-bound proposals through validation and
`AWAITING_APPROVAL`. Step 30 requires explicit authenticated human-owner
approval before the narrow technical Commit Helper and activation services can
act. Step 31 retrieves only applicable ACTIVE owner-scoped patches and yields
to canonical evidence. Step 32 preserves supersession, revocation, logical
deletion, private export, and shared-promotion review boundaries.

Personal Memory and Critic text remain private/untrusted context and never
become canonical evidence.

### Audit and human review

Step 33 audit events are typed, append-only, sequence-bound, and hash chained.
Append and read/export capabilities are separate; verification detects
modification, deletion, insertion, reordering, previous-hash changes, event
hash changes, and chain-head inconsistencies without silently repairing them.
Exports are bounded and redacted.

Step 34 reviewer access is separately authenticated and case scoped. A review
decision is not a generic database mutation, owner approval, technical commit,
activation, or source-publication capability. Downstream handoff is typed,
idempotent, and revalidates authority-bearing state.

### Optional Critic

Step 39 Critic review is additive. Strict parsed output can produce only a
Step 28 `DETECTED` candidate. Unknown claim/evidence references and owner,
tenant, route, source-authority, approval, commit, activation, reviewer, tool,
or external-action spoofing fail closed. Disabled, unavailable, malformed, and
timeout modes leave the verified core path unchanged.

### Constrained runtime

The Step 40 profile uses one web worker, hosted generation, one lazy bounded E5
instance, a remote required database in the core profile, bounded pools and
queues, external derived cache, Critic disabled by default, and ingestion off
after prepared-corpus validation. Resource pressure rejects optional/heavy
work before required correctness or authority checks and cannot disable RLS,
audit, temporal resolution, verification, or owner approval.

## Threat and regression matrix

| Control | Adversary action | Required secure result | Primary proof families |
|---|---|---|---|
| Authentication/session | forged callback, state/nonce/PKCE mismatch, stale session, fixation | fail closed; new server session only after verified callback | Step35, Step41 UI |
| Host/redirect | hostile Host, redirect origin/path manipulation, encoded traversal | reject or canonical local path; no external redirect | Step41 UI |
| CSRF | missing, cross-session, wrong-origin, duplicate token | no mutation | Step35, Step41 UI |
| XSS | hostile patch/source/model/Critic/audit text | escaped data; CSP and HTMX restrictions prevent execution | Step35, Step41 UI |
| IDOR | guessed owner/tenant slot, patch, proposal, export, review case | zero cross-owner/cross-tenant success | Step27-35, live Step36/38 |
| Input bounds | oversized body/list/number, duplicate fields, invalid UTF-8, controls | typed 4xx or contract rejection before business call | Step41 UI, contract adversarial |
| SQL injection | SQL-shaped tenant/owner/source/model text | values remain parameters; zero SQL-structure control | Step41 UI, persistence/RLS |
| FORCE RLS | supplied tenant/owner/context or role escalation | zero unauthorized read/write; required FORCE RLS remains | CockroachDB RLS, live Step36 |
| Credential separation | missing narrow capability or broad fallback attempt | fail closed; no admin/master fallback | Step36, Step37 |
| Provider injection | demand tools, evidence bypass, approval, secret disclosure | inert/rejected; output remains data | Step22/25/38/39 |
| Route/source authority | substitute HAT, tenant, unpublished source, evidence ID | hard-filter rejection; zero unauthorized evidence inclusion | Step17-21 |
| Temporal/conflict | user/model time spoof, stale/future/superseded conflict | trusted source time governs; uncertainty is not VERIFIED | Step21, Step38 |
| Correction/verifier | forged or modified claims, packet, evidence, optimistic model result | integrity or eligibility failure; no known-bad V1 fail-open | Step23-26 |
| Personal Memory | skip state, spoof owner/slot, replay quota, override evidence | exact lifecycle and owner scope; canonical evidence wins | Step27-32 |
| Commit Helper | fabricate approval or change content/hash/state | fail closed; technical commit only after exact receipt binding | Step30/36/37 |
| Audit | modify/delete/insert/reorder event or chain link | tamper detected, never repaired silently | Step33/37 |
| Review | impersonate reviewer, stale handoff, direct commit/activation | deny or revalidate; no downstream authority expansion | Step34/36/37 |
| Critic | fake evidence/claim/owner/route/authority/action | reject or Step28 candidate only; core remains available | Step39 |
| Recovery | ack loss, crash, timeout, connection failure, missing capability | bounded idempotent recovery, zero duplicate authority effects | Step37 live |
| German Law E2E | integrated model defect and Personal Memory reuse | verified canonical correction with coherent lineage | Step38 live replay/current regression |
| 4 GB profile | pressure used to skip safeguards | budget remains green; backpressure only, no authority regression | Step40 fresh validation |

## Acceptance counters

Closure requires every unauthorized-success, leak, escalation, fail-open,
undetected tested audit-tamper, and production-resource-mutation counter to be
zero. All were observed at zero. The canonical evidence digest is
`f095c4cb42ece2d1ef156b8a19233927c3bfd85c7a58feb31aae91629d0b32a7`.

Step 42 is not started by this inventory or campaign.
