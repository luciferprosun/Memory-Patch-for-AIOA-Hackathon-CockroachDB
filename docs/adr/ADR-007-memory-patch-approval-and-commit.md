# ADR-007: Separate Memory Patch Approval from Commitment

- Status: Accepted
- Date: 2026-07-25

## Context

A proposal source should not gain the authority to approve its own factual
change or write authoritative memory.

## Decision

Memory Patch lifecycle has distinct evidence binding, validation, approval,
technical commitment, and activation stages. Approval is content-hash-bound to
an exact approval identifier, proposal, owner, tenant, personal space, decision,
claimed actor, reason, and decision time. Commitment is a separate receipt from
a bounded commit service and binds the approved proposal and approval proof.

The current contract validates those claims and bindings but does not
authenticate the human actor or commit service. Those identities must be
authenticated and authorized before an application constructs the records.

## Consequences

Models, HATs, Knowledge Hub, and Critic Prompt Loop may propose but cannot
approve, commit, or activate. Rejected and revoked patches cannot return to
active. Hashes are integrity bindings, not signatures; physical service
identity remains future work. Contract-level binding also does not provide
database uniqueness, one-time approval consumption, durable replay prevention,
or transactional idempotency; those remain persistence-layer requirements.
