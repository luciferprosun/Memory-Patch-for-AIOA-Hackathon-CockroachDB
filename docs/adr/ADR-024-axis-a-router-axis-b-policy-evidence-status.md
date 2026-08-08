# ADR-024: Axis A routing, Axis B policy, and evidence status

## Status

Proposed. It becomes accepted only when the Step 17 closure commit is
reachable on `origin/main`.

## Context

Step 12 established the trusted system-installed HAT registry, and Steps 13 to
16 established and published the first real German Law HAT corpus. The Kernel
now needs a domain-neutral decision boundary before retrieval can begin.

Routing, answer policy, execution policy, evidence quality, and final answer
status are different facts. Combining them would let a HAT selection or model
claim accidentally become execution or answer authority. Creating another HAT
registry would also allow inconsistent trust decisions.

## Decision

1. Axis A is deterministic and non-authoritative. It consumes only canonical
   request data and a hash-bound snapshot of existing Step 12 registry entries.
2. Request-local candidates can narrow but cannot create trusted HAT identity.
   Unknown, disabled, untrusted, quarantined, revoked, mismatched, or
   incorrectly scoped candidates fail closed.
3. Axis A returns exactly `PASS_THROUGH`, `HAT_ASSIST`, `HAT_ENFORCE`, or
   `AMBIGUOUS`. `HAT_ENFORCE` means enforcement of a knowledge-answer policy,
   never permission to execute.
4. Axis B returns independent `KnowledgePolicyDecision` and
   `ExecutionAuthorizationDecision` records. Neither record performs an
   action or bypasses another required human gate.
5. Existing canonical `EvidenceStatus` is reused. A non-persistent closed
   `EvidenceCoverageStatus` distinguishes complete, partial, empty, and
   conflicting coverage without changing the database vocabulary.
6. Evidence status/coverage and `AnswerStatus` remain distinct typed fields.
7. Model and provider output may be data but never authority for route,
   evidence, answer, or execution decisions.
8. All decision inputs and outputs are immutable, canonically ordered,
   hash-bound, and verified before consumption.
9. Tenant and user restrictions are checked before a HAT becomes eligible.
10. Retrieval begins only in Step 18. Step 17 performs no corpus query,
    network call, provider call, subprocess, database operation, or target
    filesystem write.

## Consequences

The Kernel can select one trusted knowledge path and preserve exact policy
ceilings without creating capability. Failed mandatory eligibility and
unresolved conflicts become explicit ambiguous results rather than liberal
fallbacks. Step 18 receives a stable route identity, exact HAT identity, and
hard scope boundary.

The separate coverage qualifier is intentionally not added to the Step 4 SQL
vocabulary. Step 17 is a pure in-memory decision boundary and requires no
database migration.

## Rejected alternatives

### Let a model select the authoritative HAT

Rejected because model output is untrusted data and is not registry, policy,
or execution authority.

### Treat HAT enforcement as execution authorization

Rejected because a Knowledge HAT constrains knowledge answers only. External
effects remain subject to independent system and human gates.

### Add a second HAT registry

Rejected because Step 12 already owns trusted identity, installation, and
enablement state.

### Extend persisted evidence vocabulary in Step 17

Rejected because it would require database scope. A bounded coverage
qualifier expresses partial evidence without altering existing migrations.

### Begin retrieval inside the router

Rejected because exact and full-text retrieval are the separately bounded
Step 18 scope.
