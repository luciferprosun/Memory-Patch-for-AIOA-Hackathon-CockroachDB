# Knowledge Kernel Contract Baseline 1A

## Status and scope

This baseline is executable contract code, JSON Schema, deterministic state
machines, synthetic fixtures, and standard-library tests. It does not retrieve
knowledge, call a model, connect to a database, execute an action, or activate a
plugin.

Kernel Core is domain-neutral. German law is intended to be the first major
Knowledge HAT client, but no legal field, route keyword, legal answer, or
jurisdiction rule is part of Core. The synthetic fixtures prove the same
contracts with a software-version HAT and a fictional equipment-manual HAT.

## Contracted future flow

The contracts can represent the following later pipeline without implementing
it:

1. Record a `KernelRunIdentity`.
2. Decide Knowledge Route (Axis A) and Action Policy (Axis B) independently.
3. reference Draft V1;
4. select a system-installed Knowledge HAT;
5. retrieve and freeze an `EvidenceBundle`;
6. construct and hash a `CorrectionPacket`;
7. reference Draft V2 and record claim verdicts;
8. return a verified answer or a fail-closed answer status;
9. create a `MemoryPatchProposal`;
10. obtain an authenticated and authorized human or owner action in a future
    application and represent its exact binding as an approval contract;
11. perform a separately authorized technical commitment;
12. activate and reuse the committed patch within its scope.

These are records and authority boundaries, not a live orchestration engine.
The contract layer validates the structure and binding of an approval claim; it
does not authenticate the claimed actor. Authentication and authorization must
be supplied by a later application boundary before it creates that record.

## Independent decision axes

Axis A is exactly `PASS_THROUGH`, `HAT_ASSIST`, `HAT_ENFORCE`, or `AMBIGUOUS`.
Axis B is exactly `ALLOW`, `DENY_ACTION`, or `REQUIRE_CONFIRMATION`. Personal
memory and evidence retrieval cannot rewrite Axis B.
An informational answer can still be verified when an external action is
denied; `BLOCKED_POLICY` applies when the requested result actually requires
that denied or not-yet-confirmed action.

Evidence status is a third dimension. Answer status is a fourth. For example,
retrieval failure preserves the historical route:

```text
knowledge_route = HAT_ENFORCE
evidence_status = INSUFFICIENT
answer_status = BLOCKED_NO_VERIFIED_EVIDENCE
```

The invariant is enforced by `derive_answer_status()` and
`validate_result_invariants()`. An ambiguous route, unavailable required
storage, policy denial, evidence conflict, verification failure, and model
generation failure each have distinct result values.

## Generic scope

`ScopeDimension` carries a name, typed value, comparison mode, source, and
required flag. A HAT manifest declares supported definitions and missing-value
behavior. Core checks representation and manifest consistency but does not
interpret the meaning of a dimension.

The generic comparison modes are exact, set membership, hierarchy, range,
semantic version, timestamp, and a declared custom HAT rule. This supports
versioned software, manuals, repositories, research, or future legal validity
without adding a domain-specific field to Core.

## Evidence and correction integrity

An `EvidenceBundle` is ordered and frozen under a retrieval-policy version.
Model-experience hints and session memory cannot be constructed as evidence.
A `CorrectionPacket` binds the route, policy, scope, claims, ordered evidence,
source versions, conflicts, corrections, prohibited claims, uncertainty,
citations, and retrieval versions.

Canonical JSON uses sorted object keys, stable enum values, UTC timestamps,
deterministic set ordering, UTF-8, and strict rejection of NaN and infinity.
SHA-256 supplies deterministic identity, reproducibility, and mutation
detection. An object's own hash field is excluded from its calculation.

A hash is not a signature. It does not prove source authenticity or that a
model semantically followed a packet. HMAC and digital signatures remain
explicit future extension seams.

Approval proofs bind the approval identifier, protected proposal hash,
ownership scope, decision, claimed actor, reason, and decision time. This
prevents detaching the same proof from that exact contract object, but does not
consume the approval. Durable one-time consumption, identifier uniqueness, and
transactional idempotency require future persistence.

JSON Schema provides a strict transport shape, including a pinned schema
version. Runtime constructors and state machines provide the authority
boundary. Public construction starts in least-privileged states; privileged
lifecycle states can only be produced by validated state-machine transitions.
Validating or copying JSON alone never authorizes or reconstitutes such a
transition.

## Fail-closed boundary

Future application components must preserve the storage contract: when
configured external storage is absent or has the wrong identity, they must
return `BLOCKED_STORAGE_UNAVAILABLE`. They must not silently redirect corpora,
embeddings, indexes, snapshots, or large caches to the internal drive.

Likewise, incomplete tenant, evidence, approval, or authority validation fails
closed. This baseline performs no fallback write, external action, or live
commit.
