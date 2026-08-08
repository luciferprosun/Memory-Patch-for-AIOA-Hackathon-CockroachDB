# Axis A router, Axis B policy gate, and evidence status 1A

## Purpose

Step 17 introduces one deterministic Kernel boundary for deciding which
trusted Knowledge HAT may apply and which answer and execution policy ceilings
remain in force. It is domain-neutral, provider-independent, and incapable of
retrieval or execution.

The boundary has two independent axes:

1. Axis A consumes a canonical request, request-local candidate descriptors,
   and a hash-bound snapshot of the existing trusted HAT registry. It returns
   `PASS_THROUGH`, `HAT_ASSIST`, `HAT_ENFORCE`, or `AMBIGUOUS`.
2. Axis B consumes that exact route, canonical evidence state, request scope,
   and a trusted policy ceiling. It returns an independent knowledge-answer
   decision and execution-authorization decision.

Neither result is a human approval, execution token, provider instruction, or
permission to mutate memory.

## Trusted HAT eligibility

`TrustedHatRegistrySnapshot` contains the existing Step 12 `RegistryEntry`
records. It is a request-time, immutable view, not a second registry and not a
runtime implementation catalog. Candidates can only narrow the trusted set.
They cannot introduce an identity absent from the registry.

A candidate is eligible only when all of these facts agree:

- exact HAT ID, version, typed manifest digest, and compatible kernel API;
- registry state `ENABLED`;
- system-installed runtime binding and trusted enable receipt;
- receipt digests, capability digest, and binding identity;
- zero-authority manifest security policy;
- active, non-quarantined, non-revoked request-local disposition;
- domain, typed scope dimensions, tenant, and optional owner identity.

Unknown, disabled, untrusted, mismatched, or incorrectly scoped candidates
cannot route. A failed mandatory candidate yields `AMBIGUOUS` rather than a
more permissive fallback. Candidate and registry ordering are canonicalized.

## Axis A decisions

| Decision | Meaning |
| --- | --- |
| `PASS_THROUGH` | No eligible HAT is needed; the neutral Kernel path remains available. Axis B still decides answer and execution policy. |
| `HAT_ASSIST` | One trusted HAT may provide advisory knowledge or evidence. It gains no authority. |
| `HAT_ENFORCE` | One trusted mandatory HAT policy constrains the knowledge-answer boundary. It grants no execution authority. |
| `AMBIGUOUS` | Mandatory eligibility failed or eligible candidates conflict. The route fails closed and selects no HAT. |

The result contains stable reason codes, exact request/tenant/user identity,
registry and input hashes, optional exact HAT identity, effective scope, and a
canonical route hash. It exposes no runtime handle or callable.

## Axis B decisions

Knowledge answer policy is a separate closed decision family:

- `ALLOW_ANSWER`
- `BLOCK_ANSWER`
- `REQUIRE_CONFIRMATION`

Execution authorization is another pure metadata family:

- `ALLOW`
- `ALLOW_SCOPED`
- `REQUIRE_HUMAN`
- `DENY`

These values describe only the current policy ceiling. They do not execute an
operation and cannot bypass another approval, claim, scope, resource, or
kill-switch gate. `ALLOW_SCOPED` binds an exact canonical scope.
`HAT_ENFORCE` never upgrades the execution decision. An ambiguous route or
out-of-scope policy tightens execution to `DENY`.

## Evidence and answer separation

The existing canonical `EvidenceStatus` remains unchanged and is reused. Step
17 adds `EvidenceCoverageStatus` as a non-persistent qualifier with the closed
values `COMPLETE`, `PARTIAL`, `EMPTY`, and `CONFLICTING`. This avoids changing
the persistence vocabulary while distinguishing partial evidence from no
evidence.

Only valid pairs are accepted. Examples include:

- `SUFFICIENT + COMPLETE`;
- `INSUFFICIENT + PARTIAL`;
- `INSUFFICIENT + EMPTY`;
- `CONFLICTING + CONFLICTING`;
- `UNAVAILABLE + EMPTY`.

Partial evidence can require confirmation; empty insufficient evidence blocks
the answer. The returned `AnswerStatus` is a different enum and a different
field. Evidence facts never become answer authorization merely because they
are present.

## Determinism and integrity

All Step 17 records are frozen/slotted objects. Set-like inputs are sorted and
deduplicated, context metadata is deeply frozen and bounded, and secret- or
execution-authority-shaped keys are rejected. Existing canonical JSON and
SHA-256 helpers bind every semantic field. Axis A and Axis B verify input,
snapshot, candidate, route, and policy hashes before use. Tampering fails
closed.

Provider or model text may be context data, but it is excluded from trusted
eligibility and policy authority. The same canonical inputs yield the same
decision, identity, reason codes, and hash.

## HAT-to-Kernel return boundary

`HatKernelResult` is immutable evidence and policy metadata. It carries the
request, tenant, user, HAT identity when selected, route hash, requested and
effective scopes, evidence status and coverage, answer status, knowledge
policy, execution policy, reason codes, provenance references, and its own
hash. It contains no approval, credential, command, callable, provider secret,
or execution method.

## Isolation and effects

Private candidates bind an exact tenant and optional owner user. A mismatched
tenant or user is rejected. A global system-installed HAT remains eligible
only through the same registry, manifest, domain, and scope checks. Route
results preserve the original request, tenant, and user identities.

The routing package imports no network, provider, subprocess, database, AWS,
or filesystem-write runtime. It performs no external effects and adds no
database migration.

## Step 18 boundary and non-goals

Step 17 ends after selecting an eligible knowledge path and evaluating policy.
It does not implement exact lookup, aliases, phrase or full-text search,
metadata retrieval, scan profiles, vector search, hybrid retrieval, reranking,
retrieval SQL, corpus reads, or evidence-bundle construction. Those retrieval
interfaces begin with Step 18 and must consume the exact hash-bound Step 17
route and hard scope filters.
