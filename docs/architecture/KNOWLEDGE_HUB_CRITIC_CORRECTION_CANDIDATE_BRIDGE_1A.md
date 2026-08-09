# Knowledge Hub and Critic Correction Candidate Bridge 1A

## Boundary

Step 28 accepts bounded correction observations from trusted Kernel and Critic
Prompt Loop boundaries and records them as owner-private candidate data. A
`CorrectionCandidateEnvelope` is neither a Personal Memory patch proposal nor
canonical evidence. It cannot approve, commit, activate, retrieve, publish, or
execute anything.

There is no canonical runtime Knowledge Hub producer in the current
repository. A separate Knowledge Hub adapter is therefore `NOT_REQUIRED` in
1A. Both implemented producers use one common intake contract; a future
Knowledge Hub or external-agent adapter must remain candidate-only and pass
the same validation boundary.

## Candidate contract and producers

The immutable envelope binds the exact tenant, owner, Step 27 Personal Memory
slot, slot state/configuration identity, request/run and route lineage,
producer identity, typed trigger and reason codes, exact candidate text and
digest, bounded scope/metadata, lifecycle state, deterministic candidate ID,
and candidate hash. Only `KNOWLEDGE_KERNEL` and `CRITIC_PROMPT_LOOP` are
accepted source kinds.
The current explicitly synthetic Critic boundary preserves producer identity
and version, exact run identity, route/result input-lineage hashes, exact
candidate-text SHA-256, and the candidate content hash as untrusted
provenance. No canonical Critic runtime or prompt digest exists in this
repository; a future runtime adapter must bind its prompt digest before
intake. Critic output is not evidence or authority.

Every detected claim scope must exactly equal the route/result lineage
`effective_scope` under the repository canonical scope representation. This
means the ordered set of dimensions and every dimension's name, value, value
type, comparison mode, source, and `required` flag must match. Missing or extra
dimensions and any changed semantic field fail closed; Step 28 permits neither
scope widening nor scope narrowing.

`CorrectionCandidateSubmission` is the typed producer input and
`CorrectionCandidateIntakeReceipt` is the immutable result. The Kernel and
Critic entry points validate their exact source type before they share the
common service. Raw producer JSON never reaches persistence.

## Target slot and owner gate

Every candidate targets one existing Step 27 slot using its composite tenant,
owner, and `personal_memory_space_id` identity. Intake additionally binds the
expected slot/configuration hash and version. Only `CONFIGURED` and `ACTIVE`
slots are eligible. Unknown, cross-tenant, cross-user, suspended, archived,
delete-pending, or deleted targets fail closed; intake cannot change or
reactivate the slot.

Step 27 model bindings remain configuration only. A model binding does not
authorize candidate submission, access, approval, or memory mutation.

## Persistence, quotas, and isolation

Migration `0012_step28_correction_candidate_bridge.sql` reuses
`memory_patch.memory_patch_proposals` as the existing durable correction
carrier. Step 28 writes only rows in the `DETECTED` candidate state and does
not expose update, delete, or lifecycle-transition APIs. The row embeds the
canonical envelope and preserves exact slot, owner, route, producer, and
content-hash bindings.

The migration adds narrowly scoped INSERT policy and least-privilege grants
while retaining RLS and FORCE RLS. Candidate reads and inserts require the
Step 5 transaction-bound tenant and owner context and an eligible Step 27
slot. Python filtering is not the isolation boundary. No parallel candidate
or Personal Memory table is introduced.

Candidate count and stored-byte limits are versioned, digest-bound hard
policy. The service performs an early check, while a `BEFORE INSERT`
`SECURITY INVOKER` trigger is the independent database ceiling. The trigger
serializes competing inserts by incrementing a per-slot quota epoch, measures
the actual cumulative Step 28 carrier rows/JSONB bytes plus the incoming row,
rejects an incoming JSONB value above 8 MiB before ledger arithmetic, and uses
overflow-safe remaining-capacity comparison for the cumulative limit. It
rolls back the epoch and insert together above 128 rows or 8 MiB. It
recognizes an existing exact proposal identity before accounting, so an
`ON CONFLICT DO NOTHING` replay neither consumes quota nor fails at the
ceiling. Another owner's candidates never contribute to or consume the target
owner's quota.

Migration 0012 also materializes the canonical Step 27 `hat_scope_id` and
`slot_hash` on the slot row. CockroachDB derives and checks the owner-scope
hash, and candidate RLS requires exact equality with the current stored scope,
slot hash, configuration/quota versions and digests, and enabled exact-model
binding. The Step 27 repository remains the trusted constructor of the full
slot hash and verifies it again when rehydrating a slot. Legacy pre-0012 slots
retain a safe `NULL`/`NULL` pair and cannot receive candidates until an
ordinary typed Step 27 configuration update seals those canonical values.

## Idempotency and deterministic deduplication

Step 6 idempotency owns submission replay. The same idempotency identity and
semantic request reuses the existing result; conflicting content fails
closed. A completed exact replay remains reusable after a later slot-state or
configuration change because it performs no new candidate write; a new intake
still revalidates the current slot. A duplicate receipt binds the exact
triggering submission while the returned candidate value preserves the
original stored envelope. The semantic candidate ID excludes operational replay time and binds
producer/run provenance, owner/slot, route/result lineage, scope, exact
claims, correction text, and evidence-reference hashes. Producer metadata and
reason codes remain hash-bound in the enclosing submission but do not widen
the semantic duplicate identity. Consequently, the same candidate submitted
under a distinct idempotency key is deterministically deduplicated without
overwriting its original provenance. No semantic near-duplicate model merge
occurs.

## Candidate lifecycle and authority

Step 28 terminates at `DETECTED`:

```text
producer observation -> validated submission -> DETECTED candidate
```

There is no transition to `PROPOSED`, `EVIDENCE_BOUND`, `VALIDATED`,
`AWAITING_APPROVAL`, or `ACTIVE`. Candidate text, model output, Critic output,
and repeated submissions remain non-authoritative data. The bridge cannot
mutate source registries, HAT configuration, slot configuration, model
bindings, approvals, committed patches, execution policy, or external
systems.

## Later-step boundary

Step 29 exclusively owns Personal Memory Patch Proposal construction,
evidence/dedup/conflict/staleness validation, and the later candidate state
machine. Step 30 owns user approval, technical commit, and activation. Step 31
owns active patch retrieval and cross-model reuse; Step 32 owns shared
promotion and broader lifecycle management. NOOA, OpenShell, NVIDIA, and
other external-agent integrations are not implemented by Step 28.
