# ADR-041: Typed least-privileged human review workspace

## Status

Proposed. It becomes accepted only when the Step 34 closure commit is
reachable on `origin/main`.

## Context

Step 26 can fail closed into human review and Step 32 can produce a
de-identified shared-promotion proposal that requires review. Step 33 offers
tamper-evident audit context, but audit data cannot itself make a business
decision. The repository needs one bounded workspace that prevents
cross-tenant disclosure, model self-review, stale decisions and arbitrary
reviewer mutation.

## Decision

1. Cases are created only through typed Step 26/32 adapters. Free-form model,
   Critic and caller text cannot create a privileged case.
2. Case types, states, reviewer roles, decision families, handoff targets and
   reason codes are closed contracts. Decisions are specific to the case
   family; there is no universal approval operation.
3. Cases use deterministic identity and the lifecycle OPEN, CLAIMED,
   IN_REVIEW, then RESOLVED or ESCALATED. Stale inputs fail closed without a
   persistence transition in 1A. Every mutation binds exact
   hash, expected state/version and independent replay identity.
   Intake is valid only for unclaimed OPEN v1; both the service and database
   trigger reject a pre-advanced initial record.
4. Queue and detail projections apply minimum disclosure and fixed bounds.
   Queue items never expose raw answer or Personal Memory content.
5. Reviewer access is an explicit versioned authorization over tenant, role,
   case type and optional owner. Ordinary users receive no review-table
   privilege.
6. A decision revalidates the current case, subject, context, reviewer policy
   and required Step 33 audit-integrity result. Changed subjects make old
   decisions stale; broken required audit context fails closed or escalates.
7. Reviewer notes are bounded untrusted metadata. They are neither canonical
   evidence nor source authority.
8. Business changes occur only through typed handoff receipts. Reviewer input
   never selects SQL, a function name or an arbitrary target. Answer handoff
   does not return an answer; shared-promotion handoff does not publish a
   source or mutate private content.
9. Claim, decision and handoff actions append typed Step 33 events in the same
   short serializable transaction as their Step 34 persistence. A case is
   resolved only after the required decision, handoff and audit facts exist.
10. Separate NOLOGIN, NOBYPASSRLS reviewer and review-service capabilities,
    RLS and FORCE RLS enforce least privilege. Neither role gains Commit
    Helper, source publication, external execution or administrative power.
11. Step 35 owns Personal Memory end-user UI and remains not started.

## Consequences

Reviewers receive a deterministic queue and proof-bound context while the
business services retain their own authority. Concurrency converges through
case locking, optimistic versions and unique replay identities. The workspace
can request changes or escalation without silently editing upstream objects.

The first release is an application/service boundary rather than a browser
interface. Reviewer authentication is represented by a trusted principal and
database authorization; production credential hardening remains the later
roadmap boundary.

## Rejected alternatives

### Treat model or Critic output as a reviewer decision

Rejected because upstream model output is untrusted data and has no human
review authority.

### Let review decisions update arbitrary business tables

Rejected because it bypasses business invariants and creates an authority
escalation. Only closed typed handoffs are supported.

### Close a case when it is claimed or clicked

Rejected because resolution requires a valid decision, current subject,
successful typed handoff and mandatory audit record.

### Build the Personal Memory management UI now

Rejected because it belongs to Step 35 and would violate one-step sequencing.

`Step 35: NOT STARTED`.
