# Memory Trust and Precedence 1A

## Factual precedence

The deterministic order is:

```text
CANONICAL_SOURCE_EVIDENCE
  > SHARED_HAT_VERIFIED_MEMORY
  > PERSONAL_VERIFIED_PATCH
  > USER_ASSERTED_MEMORY
  > MODEL_EXPERIENCE_HINT
  > SESSION_MEMORY
```

`trust_rank()` and `compare_memory_trust()` implement this order. A lower-trust
record cannot silently replace a higher-trust record. Trust does not change
ownership: a trusted personal patch remains private to its owner.

Canonical evidence is deliberately not a `MemoryItem` and is not a valid
Memory Patch target. A future controlled ingestion/publication pipeline is the
only place that may create canonical source evidence.

## Explicit conflicts

Conflict classes are no conflict, lower-trust conflict, same-trust conflict,
temporal conflict, scope conflict, and source conflict. Lower-trust conflicts
may be resolved by precedence while preserving a conflict record. Same-trust
conflicts remain unresolved and visible. Temporal, scope, and source conflicts
must be handled against the relevant HAT contract.

Stale or expired personal memory is excluded from retrieval, not silently
treated as current.

## Preferences are not facts or policy

Personal preferences may affect language, formatting, explanation depth,
presentation, and workflow preferences. The contract rejects preference keys
that attempt to alter canonical evidence, temporal validity, scope constraints,
Action Policy, security policy, or approval requirements.

`MODEL_EXPERIENCE_HINT` is advisory. It may later prioritize verification,
suggest a HAT, flag repeated model-version failures, or suggest a correction
strategy. It can never serve as factual evidence, activate memory, approve a
patch, or authorize an action.

## Patch targets

The only patch targets are session, user Personal Memory HAT, and shared
Knowledge HAT. Session memory requests session trust. Personal memory has exact
tenant/user/space ownership. Shared memory requires a separate domain-reviewed
proposal and shared-HAT trust. None of these paths creates canonical evidence.
