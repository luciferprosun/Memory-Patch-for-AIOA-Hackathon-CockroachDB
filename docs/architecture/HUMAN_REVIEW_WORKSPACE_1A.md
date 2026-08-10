# Human Review Workspace 1A

## Boundary and authority

Step 34 adds a bounded, human-only review workspace over exact review-required
outputs already created by Step 26 and Step 32. It does not let free-form
model, Critic or user text create a privileged case. Trusted adapters accept
only reconstructed `HumanReviewRequired`, `BoundedAnswerFailure` and
`SharedMemoryPromotionProposal` contracts and preserve their immutable
hashes.

Review is neither canonical evidence nor arbitrary business authority. A
decision cannot execute an external action, publish a Source Registry item,
activate a patch, return an answer directly or run retrieval. Step 35 Personal
Memory end-user UI is absent.

## Closed case inventory and lifecycle

`ReviewCaseType` contains only currently implemented review inputs:

- answer verification, conflicting, insufficient, stale and confirmation
  cases from Step 26; and
- shared-memory promotion, privacy-review and canonical-conflict cases from
  Step 32.

Every type fixes its subject family, required context, allowed decision
family, reviewer role and typed downstream target. Arbitrary strings are
rejected.

`HumanReviewCase` is immutable and canonical-hash-bound. Its deterministic
trigger binds every immutable intake semantic: type, tenant/owner, subject,
request/route, reasons, priority, exact source/audit/context references and
verification status. Exact intake replay returns the current persisted case,
even after its lifecycle advances; changed content under that identity fails
closed. The only implemented progression is:

```text
OPEN (v1) -> CLAIMED (v2) -> IN_REVIEW (v3)
             -> RESOLVED / ESCALATED (v4)
```

There is no generic state setter. Each transition reconstructs the contract,
checks the expected hash/version and is guarded again in CockroachDB.

## Queue, claiming and concurrency

The queue requires an authenticated typed `ReviewerPrincipal` and a matching
persisted `ReviewerAuthorization`. The versioned access policy binds role,
case type, tenant and optional exact owner scope. `ANSWER_REVIEWER` and
`SHARED_MEMORY_REVIEWER` see only their case families; `SENIOR_REVIEWER` may
receive either only when an explicit authorization exists.

Queue views disclose case type, priority, stable owner-scope digest, state,
age identity and a fixed safe summary. They do not expose raw answer or
Personal Memory text. Results use deterministic priority, creation-time and
case-ID order, a maximum page size of 100 and a filter-bound sequence cursor.

`ClaimReviewCaseRequest` binds the exact OPEN case, reviewer principal,
expected version and replay identity. A short Step 6 serializable transaction
locks the case and records one claim, one state transition and one Step 33
audit event. Competing claims cannot both win. Exact replay returns the same
receipt; changed replay is rejected.

## Minimum-disclosure detail and audit integrity

`HumanReviewDetailProjection` carries only the case-specific source context,
bounded audit event hashes and exact Step 33 chain-verification result hash.
Answer context contains route/evidence/verification status and immutable
references, never a known-bad Draft V1 fallback. Shared-promotion context
contains de-identification, privacy, consent and canonical-conflict state;
the raw private source is not automatically shared.

The Step 33 `AuditChainVerificationResult` is reconstructed before an adapter
uses it. Its last event must be the exact source audit event. A case records
whether the source range verified. When audit integrity is false, ordinary
case decisions fail closed; only an explicitly allowed escalation path can
remain available. The workspace never repairs the chain.

## Human decision and typed handoff

`SubmitReviewDecision`, `HumanReviewDecision` and
`HumanReviewDecisionReceipt` bind the current case/subject hash, reviewer
principal and role, case-specific decision type, bounded reason codes and
note digest, detail hash, audit-verification hash, policy digest, expected
state/version and idempotency identity. Notes are bounded untrusted text, are
not evidence and are scanned for secret-shaped content.

Before recording a decision the service reloads authorization, case, source
context and audit references in one short transaction. A changed case,
subject, context or policy prevents the transition. One replay identity
cannot produce two semantic decisions.

The decision does not issue SQL chosen by the reviewer. A
`ReviewDecisionHandoffRequest` targets the closed service adapter derived
from the case type. `ReviewBusinessHandoffResult` and
`ReviewDecisionHandoffReceipt` state only that the exact decision was
accepted by that typed boundary. Answer handoff returns no answer; shared
handoff publishes no source and mutates no private patch. The dedicated
review service revalidates the current subject hash and receipt lineage
immediately before resolution. Handoff replay is independently protected.

## Persistence and least privilege

Migration `0017_step34_human_review_workspace` adds five Step 34 tables for
authorizations, cases, claims, decisions and handoffs. Relations bind tenant,
owner, subject, state/version and canonical JSON projections. Claims,
decisions and handoffs are append-only. The case trigger rejects any initial
row other than unclaimed `OPEN` v1, then accepts only the exact lifecycle
transition when its corresponding immutable child and Step 33 audit event
already exist in the same transaction. SQL shape checks bind the closed Step
26/32 source-contract family and its exact subject reference.

Two NOLOGIN, NOBYPASSRLS capabilities are separate:

- `mp_human_reviewer` may read authorized cases and append exact claim or
  decision facts; and
- `mp_review_service` may intake a typed case and perform an exact receipt
  handoff.

Neither inherits app runtime, Commit Helper, schema owner, publication or
administrative authority. All five tables have RLS and FORCE RLS. Step 34
also adds narrowly scoped policies for its typed events in the existing Step
33 audit tables without changing chain semantics. Ordinary application users
have no review-table grant.

## Bounds and later-step boundary

Queue pages, decision reasons, notes, context references, audit references
and serialized context are bounded. All SQL is parameterized and tenant
scoped. The controlled closure makes no provider/model, web, AWS/S3,
retrieval, source-publication or external-execution call.

Step 35 owns the Personal Memory end-user UI, including slot, patch,
model-binding, export and deletion screens. Step 34 exposes service contracts
only and does not start that UI.
