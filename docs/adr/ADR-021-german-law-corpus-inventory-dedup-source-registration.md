# ADR-021: Keep corpus inventory derived, immutable, and non-authoritative

- Status: accepted for Step 14 closure
- Date: 2026-08-01

## Context

The owned German Law library is much larger than repository evidence and
contains raw archives, normalized projections, manifests, QA material, and
other formats. A production inventory must survive interruption on a low-memory
machine without modifying the library, fabricating legal facts, or creating a
second source registry.

## Decision

Use a deterministic streaming inventory with a Step-14-owned disk spool and
atomic external bundle under the Step 8 `corpora/manifests` boundary. Treat raw
SHA-256 plus byte length as exact-content identity and root-relative path as a
separate provenance alias. Permit normalized duplicate evidence only through
existing strict Step 11 text and JSON normalization. Treat near duplicates as
bounded model-free review candidates, never equivalence or deletion decisions.

Keep license, privacy, verification, and quarantine as explicit typed evidence
with unknown and conflict states. Store no raw excerpts in repository evidence.
Map eligible observations into existing Step 9 registration records using Step
13 source classes and deterministic Step 6 operation identities. Registration
starts unpublished and cannot confer legal, HAT, model, approval, commit, or
external-action authority.

Large file records and candidate sets remain on the approved external volume.
Git contains only code, small synthetic fixtures, sanitized aggregate evidence,
documentation, and digests. No new database migration is needed because the
existing Step 9 registry and Step 6 idempotency schema express the required
metadata boundary.

## Consequences

- the source corpus remains byte-for-byte and structurally unchanged;
- inventory replay and resume are verifiable and fail closed on mutation;
- duplicate metrics cannot trigger destructive cleanup;
- unknown rights or privacy cannot silently become acceptable;
- registration evidence remains separate from publication and legal validity;
- the external SQLite spool has no semantic authority and is removed on
  successful completion;
- temporal/jurisdiction normalization and publication remain deferred.

## Rejected alternatives

- Loading the corpus into memory is unsafe on the target machine.
- Using filenames, timestamps, or language as source authority or legal-time
  identity is incorrect.
- Generic PDF/OCR/archive parsing would cross Step 11 and Step 14 boundaries.
- Embeddings or model similarity would be non-deterministic and premature.
- Deleting or hardlinking duplicates would destroy provenance.
- A new registry table would compete with the established Step 9 authority
  boundary.
