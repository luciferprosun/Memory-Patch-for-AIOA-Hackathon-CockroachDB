# ADR-007: Separate Memory Patch Approval from Commitment

- Status: Accepted
- Date: 2026-07-25

## Context

A proposal source should not gain the authority to approve its own factual
change or write authoritative memory.

## Decision

Memory Patch lifecycle has distinct evidence binding, validation, approval,
technical commitment, and activation stages. Approval is content-hash-bound to
an exact owner or human reviewer. Commitment is a separate receipt from a
bounded commit service.

## Consequences

Models, HATs, Knowledge Hub, and Critic Prompt Loop may propose but cannot
approve, commit, or activate. Rejected and revoked patches cannot return to
active. Hashes are integrity bindings, not signatures; physical service
identity remains future work.
