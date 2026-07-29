# Source Registry, Provenance, and Publication States 1A

## Status and boundary

Step 9 adds a typed source control plane on top of the Step 4 schema, Step 5
tenant isolation, and Step 6 transaction and idempotency foundation. It
records source identity, scope, lineage, eligibility, and publication facts.
It does not fetch, parse, chunk, answer from, approve, commit, execute, or
upload source content.

CockroachDB remains the system of record. The validated runtime is pinned to
`v26.2.4`, and migration `0006` has SHA-256
`921f5e1bb16142c082b1e91fbbaae729af3aad6f62fd0a5a0a15cda5f3fa5347`.
Migrations `0001` through `0005` remain byte-for-byte unchanged.

Step 7 and Step 8 are explicitly deferred and incomplete. No AWS operation,
S3 adapter, external-volume production adapter, NVIDIA/NOOA/OpenShell
integration, model-provider dependency, or Step 10 ingestion saga is part of
this boundary.

## Typed registration

`SourceRegistryRecord` binds one source to:

- tenant, source, and HAT-scope identities;
- shared or exact user-private ownership;
- authority level and authority basis without making authority
  jurisdiction-specific;
- license state and reference;
- access and redaction states;
- canonical scope dimensions and deterministic scope digest;
- explicit parser name, version, and contract version;
- explicit transformation name, version, and contract version;
- origin metadata;
- exact artifact digest, byte length, media type, and creation time;
- existing snapshot and knowledge-version identities;
- an optimistic publication pointer.

New registrations must begin at the exact genesis tuple:

```text
state = REGISTERED
sequence = 0
event digest = canonical publication genesis digest
```

The typed service, repository, table constraint, and INSERT RLS policy all
enforce the same rule. A caller cannot register a source already marked
eligible or published.

Authority metadata is neutral policy input. An authority label never grants
answer, approval, commit, execution, external-action, or Control Write
authority.

## Database model

Migration `0006_step9_source_registry_provenance_publication_states` adds:

| Table | Purpose | Runtime grants |
|---|---|---|
| `memory_patch.source_registry_entries` | Immutable source facts plus one compare-and-set publication pointer | SELECT, INSERT, UPDATE |
| `memory_patch.source_provenance_edges` | Immutable, append-only lineage edges | SELECT, INSERT |
| `memory_patch.source_publication_events` | Immutable, append-only publication events | SELECT, INSERT |

All three tables have RLS and FORCE RLS enabled, are owned by
`mp_schema_owner`, and use the established Step 5 request context. The runtime
has no DELETE grant. Shared access requires an exact tenant context.
User-private access additionally requires the exact owner and personal-memory
scope. A table owner without request context remains constrained.

The registry UPDATE grant is limited by an identity guard: source identity,
scope, authority, license, parser, transformation, artifact, and digest facts
are immutable. Only the publication pointer and update time may advance, and
the pointer must match the appended event.

## Provenance DAG

A provenance edge binds:

```text
tenant + source + HAT scope
parent artifact digest -> child artifact digest
edge kind
parser identity and version
transformation identity and version
canonical metadata
created time
```

The edge digest is deterministic over all canonical edge facts. Exact replay
is idempotent; the same edge identity with different facts is a conflict.
Self-edges are rejected by both typed and SQL constraints. The typed graph
rejects cycles before persistence and has a bounded node count.

Publication eligibility binds every edge and every root reachable backward
from the terminal artifact. Input order cannot change the lineage digest, a
change in any reachable branch does change it, and disconnected edges do not.
A nonempty graph from another tenant, source, or HAT scope fails closed.

## Eligibility

Policy version `source-publication-eligibility-1a` evaluates frozen facts
without changing publication state. The decision digest binds:

- registry and scope digests;
- all reachable lineage roots and the full lineage digest;
- terminal artifact digest;
- snapshot and knowledge-version identities;
- policy version, reasons, outcome, and evaluation time.

The policy fails closed for unknown or prohibited license state, missing
authority basis, mismatched private/shared scope, pending or rejected
redaction, unverified exact bytes, missing required identity, derived content
without parent lineage, quarantine reasons, and recorded conflicts.

Eligibility is evidence for a transition, not authority. It cannot publish a
source by itself.

## Publication states and events

The state vocabulary is:

```text
REGISTERED
REVIEW_REQUIRED
ELIGIBLE
PUBLISHED
QUARANTINED
WITHDRAWN
REJECTED
```

Only declared edges are legal. In particular, direct
`REGISTERED -> PUBLISHED` is forbidden, `REJECTED` is terminal, and entering
`ELIGIBLE` or `PUBLISHED` requires a fresh passing eligibility decision.

Every transition appends one event with a monotonic sequence number,
from/to state, trusted typed actor and actor reference, policy and eligibility
digests, reasons, optional reviewer reference, previous-event digest,
canonical event digest, and creation time. The actor reference is part of
both the event digest and the Step 6 idempotency request binding.

The event table is append-only. UPDATE and DELETE are not granted. The
previous-event digest, sequence, state edge, and current registry pointer are
checked as one transaction. A stale compare-and-set changes no row. A bad
event link rolls back the event, Step 6 operation record, and pointer update
together.

Only `TRUSTED_APPLICATION`, `HUMAN_REVIEWER`, and `MIGRATION_SERVICE` are
publication actor types. `MODEL`, `HAT`, and `CRITIC` are rejected. Even a
valid publication actor receives no approval, commit, execution, or answer
authority.

## Step 6 reuse

`SourceRegistryService` uses the existing
`SerializableTransactionRunner` and `IdempotencyService` for:

- `SOURCE_REGISTER`;
- `PROVENANCE_EDGE_APPEND`;
- `PUBLICATION_STATE_TRANSITION`.

The durable operation binds tenant/user context, operation identity,
idempotency key, request digest, scope digest, result reference, and result
digest. A semantic retry with an automatically generated event time retains
the same request digest; an explicitly supplied time remains part of the
binding. A different actor reference or other request fact conflicts.

The transaction runner retries SQLSTATE `40001` only, with at most ten full
transaction attempts. No model, HTTP, AWS, or other external call occurs
inside these transactions.

## Validation and limitations

The tracked evidence is
[`step9-source-registry-validation.json`](../evidence/cockroachdb-v26-2/step9-source-registry-validation.json).
It records a fresh official runtime, six migrations from zero, checksum no-op
replay, a second fresh schema with identical schema/security digest, 48 live
probes, 74 focused Step 9 tests, and complete cleanup.

The live environment was a disposable insecure single node bound only to
loopback with external I/O disabled and synthetic source facts. It proves SQL
schema, transaction, RLS, provenance, publication, denial, and cleanup
semantics. It does not prove production certificates, end-user
authentication, distributed behavior, real-source acquisition, S3, Step 8,
or Step 10.
