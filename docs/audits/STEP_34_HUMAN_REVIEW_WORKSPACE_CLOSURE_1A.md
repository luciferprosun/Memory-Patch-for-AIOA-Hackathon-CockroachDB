# Step 34 Human Review Workspace Closure 1A

## Starting point and scope

- Exact Step 33 base: `6f8f14b8acde20a8044d929ba7f6582f2c36785b`.
- Step 33 was complete and pushed; Steps 34 and 35 were not started.
- Scope is limited to typed human-review cases, least-privileged queue/detail,
  claiming, case-specific decisions, typed handoff and Step 33 audit binding.
- Step 35 remains not started. This record does not invent the final Step 34
  Git closure SHA before that commit exists.

## Contracts and lifecycle

Step 34 introduces immutable canonical-hash-bound reviewer principal and
authorization, review source context, case, queue/cursor, claim request and
receipt, detail projection, decision command/decision/receipt, typed handoff
request/result/receipt and versioned access/decision policies.

Only typed Step 26 answer-review and Step 32 shared-promotion inputs may open
a case. Closed case and decision families reject arbitrary model/user
strings. The guarded lifecycle is OPEN v1, CLAIMED v2, IN_REVIEW v3, then
RESOLVED or ESCALATED v4. Stale inputs fail closed without creating a terminal
state transition. No generic state setter exists.

Case identity binds every immutable intake semantic, including exact type,
tenant/owner, subject, request/route, reason, priority, source/audit/context
and verification fields. Case creation, claiming, decision and handoff each
have independent semantic replay protection. Exact intake/claim retries read
back the current progressed case without duplicating events. Expected
state/version and current hashes prevent two reviewers winning, incompatible
terminal decisions and stale handoffs.

Controlled evidence uses a fixed reviewer order so its hashes are byte-stable;
the focused concurrency test independently runs two real reviewer threads and
proves that only one can claim the same OPEN snapshot.

## Access, context and authority

Queue access requires an authenticated reviewer principal and explicit
tenant/role/case-type/optional-owner authorization. Queue projection is
minimum-disclosure and deterministically bounded. Detail binds the exact
source context and Step 33 verification/result hashes; broken required audit
context fails closed rather than being repaired.

Reviewer notes are bounded untrusted metadata, not canonical evidence.
Human decisions cannot select arbitrary SQL or a dynamic service target.
Typed answer handoff returns no answer, and typed shared-promotion handoff
publishes no source and mutates no private patch. Model and Critic reviewer
authority, canonical publication authority and external-execution authority
remain false.

## Persistence decision

Migration `0017_step34_human_review_workspace` adds five owner/tenant-bound
tables and narrowly extends the Step 33 event registry/policies for Step 34
actions. Claims, decisions and handoffs are append-only. The guarded case
trigger rejects non-OPEN initial rows and requires the corresponding immutable
child and audit event for every later transition in the same short
serializable transaction.

Separate NOLOGIN, NOBYPASSRLS `mp_human_reviewer` and `mp_review_service`
roles have exact table/function privileges, RLS and FORCE RLS. Neither role
inherits app runtime, Commit Helper, schema ownership, source publication or
admin authority. Ordinary application runtime has no review-table access.

## Validation and known limitations

The final evidence file is
`docs/evidence/review/step34-human-review-workspace-validation.json`. It binds
the disposable CockroachDB migration replay/catalog, real Step 26 and Step 33
fixtures, sanitized typed Step 32 review context, case/claim/decision/handoff
matrix, authorization negatives, verified audit chain and exact cleanup.

The final controlled run passed with migration 0017 applied once and replayed
idempotently across all 17 migrations. It persisted two cases, two claims, two
decisions and two handoffs; verified a 12-event Step 33 chain; denied ordinary,
cross-tenant and unrelated-private-memory access; and removed the disposable
database, roles, store, process and ports without forced termination. The
database also denied a direct non-OPEN initial case with SQLSTATE `23514`, and
exact case/claim retries returned the current progressed state. The
canonical validation digest is
`43cc16fe2381603deec3179aca29864c4186d5f14eb4b965779f6c4c643c27e0`.

Known limitations are explicit:

- the workspace is a service/application boundary, not a browser UI;
- the shared-promotion edge uses sanitized typed context in the controlled
  run, while contract tests exercise the full immutable Step 32 adapter;
- review-service handoff acknowledges acceptance by an exact existing
  boundary and does not claim source publication or answer delivery;
- production credential hardening remains Step 36; and
- Step 35 Personal Memory end-user UI is absent.

`Step 35: NOT STARTED`.
