# ADR-008: Keep the Critic Prompt Loop Proposal-Only

- Status: Accepted
- Date: 2026-07-25

## Context

A future Critic Prompt Loop can detect recurring model errors, but automatic
self-approval would collapse detection, trust, ownership, and authority.

## Decision

The neutral `CorrectionCandidate` vocabulary ends at `DETECTED` or `PROPOSED`.
It binds tenant, user, personal space, run, model binding, claims, correction,
evidence references, uncertainty, timestamp, and content hash.

## Consequences

Critic output is not committed memory, canonical evidence, active memory, or
action authorization. It enters the ordinary Memory Patch evidence and human
approval process and cannot cross owners.
