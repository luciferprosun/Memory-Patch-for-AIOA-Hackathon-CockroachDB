# Step 24 - Correction Packet Construction and Integrity 1A

## Verdict

`COMPLETE AND PUSHED` once this closure record is reachable on `origin/main`.
Step 25: NOT STARTED.

## Starting identity

- Exact Step 23 baseline: `f328168434984ea022346758770d6df23c67bb08`
- Baseline subject: `feat(modeling): add claim extraction and evidence binding 1a`
- Branch: `main`
- Baseline tests: 1,532 passed
- Baseline focused Step 17-23/authority/tenant/persistence/serialization:
  629 passed
- Baseline contract validator and compileall: PASS

The final closure identity is the commit containing this record. No future
commit SHA is embedded before that commit exists.

## Implemented packet contracts

Step 24 adds immutable/hash-bound `CorrectionPacketV1A`,
`RequiredCorrection`, `ProhibitedClaim`, `CorrectionCitation`,
`CorrectionConflict`, `CorrectionFactReference`, fixed packet-policy and
knowledge-policy binding contracts. The packet accepts only the verified Step
23 `PacketInputSnapshot`; it copies no ad-hoc claim/evidence/correction input
and revalidates every nested hash.

Request, tenant, user, route, selected HAT/version/manifest, HAT scope,
effective scope, Draft V1, Step 20 bundles, Step 21 resolution and evidence
status, Step 23 snapshot, ordered claims, and candidate assessments are bound
into packet identity. Step 20 policy decisions remain cryptographically bound
by exact bundle hashes and are not guessed or reinterpreted because the Step
23 snapshot intentionally does not copy their enum values.

## Correction, prohibition, citation, and conflict policy

Fixed deterministic rules preserve supported claims, remove/prohibit refuted
claims, qualify unverified assertions, create explicit temporal and
source-authority corrections, and preserve unresolved conflicts without a
winner. Non-factual segments receive no factual correction. Structured
replacement-fact references bind exact Step 23 links/citations and never
invent replacement text.

Citations preserve link, candidate, source/version/chunk, content, authority,
publication, temporal-assessment, and relation identities. Full corpus chunks,
secret URLs, provider data, and machine paths are not copied into packets.
Conflicts retain both supporting and refuting hashes and require
`PRESERVE_AND_QUALIFY`; Step 20 rank and vector similarity remain
non-authoritative.

## Canonical JSON, hash, HMAC, and replay

The existing canonical serialization helpers are reused. Claims,
assessments, corrections, prohibitions, citations, conflicts, limitations, and
reason codes use deterministic ordering. `packet_id`, scope binding, packet
policy, and `packet_hash` bind all semantic fields. No runtime timestamp enters
the semantic packet, and exact replay is byte/hash stable.

`CorrectionPacketIntegrityReceipt` provides a separate domain-separated
HMAC-SHA-256 boundary. Runtime key material is never serialized, logged,
committed, or sent to a model/provider. The signer enforces a 32-byte minimum,
redacts its representation, binds the key ID, and uses constant-time
verification. Unit and controlled validation prove correct receipt replay,
wrong-key rejection, packet/receipt tamper rejection, and absence of
production key material from artifacts. The committed deterministic test
vector is explicitly public and non-production.

## Persistence and migration decision

No migration is added. Step 4 already owns `correction_packets` and
`correction_requirements`, and Step 5 already protects them with RLS. Safe
insertion requires coordinated durable kernel-run, Draft, claim, HAT-scope,
route, and action-policy lineage. Step 23 deliberately did not overload final
claim-verdict persistence and its frozen snapshot does not expose explicit
legacy route/action-policy enum columns.

Step 24 therefore does not guess security-semantic values or independently
insert missing upstream rows. Durable persistence is explicitly deferred to a
future coordinated orchestration boundary; existing Step 4 tables remain the
only approved target. No competing packet store exists.

## Validation

- Step 24 focused suite: 30/30 PASS.
- Full repository suite: 1,562/1,562 PASS.
- Step 17-23, authority, tenant, CockroachDB persistence/schema, HAT and
  serialization regressions: 659/659 PASS.
- Contract validator: PASS.
- Compileall: PASS.
- Controlled offline Step 24 validation and committed-evidence replay: PASS.

The controlled validator retains the committed Step 23 validation and
snapshot hashes as real upstream closure identity. The committed artifact does
not serialize a production Step 23 snapshot, so semantic pipeline and edge
fixtures are explicitly synthetic and bounded. No fabricated real-corpus
packet claim is made.

Sanitized evidence is committed at
`docs/evidence/modeling/step24-correction-packet-validation.json`.

## Authority, isolation, effects, and limitations

Snapshot, packet, citation, conflict, receipt, tenant/user/route/HAT/scope,
and source-authority detachment fail closed. A packet cannot change evidence
status, route, source authority, publication state, approval, execution, HAT
activation, or Personal Memory.

There are zero retrieval, model, provider, network, database, AWS, S3,
approval, and execution calls in controlled validation. No external runtime is
started, so cleanup is `NOT_REQUIRED`.

The packet contains deterministic correction directives and immutable evidence
references, not a corrected prose answer. HMAC production-key provisioning and
coordinated durable persistence remain explicit integration limitations.

## Step 25 handoff

Step 25 may consume verified `CorrectionPacketV1A`, its packet hash, a valid
`CorrectionPacketIntegrityReceipt`, exact Draft V1, ordered Step 23 claims and
candidate assessments, required corrections, prohibited claims, citations,
and preserved conflicts. It must verify packet hash/HMAC before any corrected
generation and may not change route, scope, source authority, or upstream
evidence status.

Step 25 remains NOT STARTED in this closure.
