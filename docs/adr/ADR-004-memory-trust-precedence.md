# ADR-004: Make Memory Trust Precedence Deterministic

- Status: Accepted
- Date: 2026-07-25

## Context

Canonical evidence, reviewed shared memory, personal corrections, assertions,
model hints, and session context can disagree.

## Decision

Use the fixed factual order documented in
`MEMORY_TRUST_AND_PRECEDENCE_1A.md`. Preserve conflicts explicitly. A lower
class cannot silently override a higher one, and same-class conflicts remain
unresolved.

## Consequences

Personal preferences can shape presentation but cannot rewrite facts, time,
scope, action policy, security, or approval. Canonical evidence remains outside
Memory Patch targets.
