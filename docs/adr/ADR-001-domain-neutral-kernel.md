# ADR-001: Keep Kernel Core Domain-Neutral

- Status: Accepted
- Date: 2026-07-25

## Context

The first planned production Knowledge HAT serves German law, while later HATs
may serve software, repositories, security, research, or private documentation.
Embedding the first client's vocabulary in Core would make routing, evidence,
and memory lifecycles domain-specific.

## Decision

Kernel Core carries generic domain IDs, typed scope dimensions, comparison
modes, validity intervals, source versions, and HAT-declared policies. It does
not require jurisdiction or any other client-specific field. Two unrelated
synthetic HAT fixtures must validate through the same contracts.

## Consequences

Each HAT owns its domain interpretation. New HATs do not change routing enums or
Memory Patch states. German law remains a future client and no legal conclusion
belongs in Core.
