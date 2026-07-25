# ADR-009: Treat Model Experience as Advice, Not Evidence

- Status: Accepted
- Date: 2026-07-25

## Context

Repeated failures from a model version can improve verification priorities,
but those observations do not establish facts about an external domain.

## Decision

`ModelExperienceEvent` records provider, family, exact version, failure and
claim categories, run, correction/verifier outcomes, owner scope when
applicable, and bounded retention. Its trust class is advisory and
`EvidenceItem` rejects it.

## Consequences

Future routing may prioritize a HAT or verification based on experience.
Experience cannot override canonical evidence, approve or activate memory, or
authorize actions.
