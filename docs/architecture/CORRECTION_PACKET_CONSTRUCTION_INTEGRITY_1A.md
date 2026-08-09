# Correction Packet Construction and Integrity 1A

## Scope and upstream boundary

Step 24 consumes exactly one immutable Step 23 `PacketInputSnapshot`. The
builder verifies the snapshot hash and every nested Step 23 claim, link, and
assessment hash before deriving anything. It accepts no ad-hoc claim list,
evidence list, replacement fact, retrieval result, model output, or caller
policy.

The resulting `CorrectionPacketV1A` binds the exact request, tenant, user,
route hash, selected HAT identity and manifest, HAT scope, effective scope,
Draft V1 identity, Step 20 bundle identities, Step 21 result, and Step 23
snapshot. Step 20 knowledge-policy decisions are not copied or guessed because
the Step 23 snapshot deliberately carries them as immutable bundle hashes. A
typed `KnowledgePolicyBinding` preserves those hashes and the exact Step 21
evidence status; `explicit_decision_values_available=false` records this
limitation honestly.

The earlier foundation contract in `contracts.correction` remains stable.
Step 24 uses a versioned `CorrectionPacketV1A` because its exact Step 23
lineage and richer correction/citation integrity semantics cannot be added to
the original contract without breaking its public hash meaning.

## Deterministic correction rules

Step 24 does not call a model. It applies one fixed policy:

- `SUPPORTED` creates no factual correction, while its admitted evidence may
  become a required citation.
- `REFUTED` creates a `REMOVE_CLAIM` requirement and a
  `DO_NOT_REPEAT_EXACT` prohibition. The exact original Draft V1 span remains
  unchanged and inspectable.
- generic `UNVERIFIED` factual content must be qualified and cannot be stated
  as fact.
- a temporal mismatch creates `TEMPORAL_CORRECTION` and prevents use outside
  the proven temporal scope.
- an authority mismatch creates `SOURCE_AUTHORITY_CORRECTION` and prevents an
  authority upgrade.
- a material conflict creates a qualification requirement, preserves both
  sides, and prohibits presenting an unresolved winner as certain.
- non-factual segments create neither factual corrections nor prohibitions.

`CorrectionFactReference` values contain only immutable evidence/link,
candidate, content, temporal-assessment, relation, and citation identities.
They never fabricate a replacement sentence. When the evidence universe does
not contain a structured replacement fact, the packet requires removal or
qualification instead of inventing one.

## Claims, citations, and conflicts

The packet reuses Step 23 `ClaimRecord` values. Every `RequiredCorrection` and
`ProhibitedClaim` links to an existing claim ID. Citations bind the exact Step
23 evidence-link hash, Step 20 candidate identity, source/version/chunk and
content hashes, source reference, authority and publication state, Step 21
temporal assessment, and relation. No new evidence is retrieved.

Only already-published official-primary or authoritative-secondary evidence
can cross the Step 23 boundary and become a citation. Citation strings reject
common presigned-URL markers and canonical serialization rejects sensitive
machine-specific paths. Full evidence chunks are not duplicated into the
packet.

`CorrectionConflict` groups preserve exact supporting and refuting link
hashes, temporal assessments, affected claims, authority metadata, and the
upstream conflict identity. The fixed handling is
`PRESERVE_AND_QUALIFY`; Step 20 rank and vector similarity cannot choose a
winner.

## Ordering, canonical JSON, and packet identity

All records are frozen dataclasses. Claims retain Step 23 span order.
Corrections and prohibitions order by claim position, closed action type, and
deterministic identity. Citations order by claim position, relation, lineage,
candidate identity, and citation identity. Conflicts order by conflict-group
ID. Set-like hashes and limitations are sorted and deduplicated.

The repository's existing `canonical_json_bytes` and `canonical_sha256`
helpers are reused. No second serializer exists. `packet_id` deterministically
binds request ID, Draft V1 hash, Step 23 snapshot hash, and schema version.
`packet_hash` binds every semantic packet field, including ordered membership,
policy, scope, lineage, limitations, and public integrity metadata. Runtime
timestamps are absent, so exact replay is byte/hash stable. Canonical packets
are bounded to 4 MiB.

## HMAC boundary

The public packet hash provides deterministic identity and mutation detection.
Authenticity is a separate `CorrectionPacketIntegrityReceipt` using
HMAC-SHA-256 over the domain-separated message:

```text
MEMORY_PATCH_CORRECTION_PACKET_V1 \0 packet_hash
```

The receipt contains only packet hash, algorithm, key ID, authenticator,
domain ID, and its own receipt hash. Runtime key material is never serialized
into the packet or receipt and is never sent to a model/provider. The signer
requires at least 32 bytes, redacts its representation, uses constant-time
comparison, and rejects packet, receipt, key-ID, or authenticator tampering.
Production key retrieval/configuration is outside this repository step; tests
and controlled validation use an explicitly public, non-production
deterministic test vector that is not a secret.

## Persistence decision

No migration is added. Step 4 already provides `correction_packets` and
`correction_requirements`, protected by the Step 5 RLS boundary. Safe writes,
however, require the corresponding durable kernel run, Draft V1, HAT scope,
claims, and explicit route/action-policy columns. Step 23 intentionally froze
candidate assessments without independently persisting claims, and its
snapshot does not copy the route/action-policy enum values.

Step 24 therefore does not guess those values, insert missing upstream rows,
or overload final-verdict storage. Persistence is explicitly deferred until a
coordinated orchestration transaction has the complete durable upstream
lineage. The canonical in-memory packet, hash, and optional HMAC receipt are
the Step 24 output. Existing tables remain the only approved future durable
target; no parallel store is introduced.

## Authority and Step 25 boundary

The packet can preserve knowledge policy, evidence status, conflicts,
freshness, citations, and later drafting constraints. It cannot change route,
HAT, tenant, user, source authority, publication state, evidence status,
approval, execution, or Personal Memory.

Step 25 is NOT STARTED. Draft V2 generation, a corrected provider call,
layered final claim verification, and verified-answer assembly remain outside
Step 24.
