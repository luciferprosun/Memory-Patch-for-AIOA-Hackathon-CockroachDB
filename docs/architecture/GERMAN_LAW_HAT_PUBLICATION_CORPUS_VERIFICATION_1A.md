# German Law HAT publication and corpus verification 1A

## Purpose

Step 16 is the trusted publication boundary between the immutable Step 14
inventory, the reviewable Step 15 normalization bundle, and later
retrieval-oriented work. It creates deterministic publication evidence for
eligible German-law versions. It does not answer questions, retrieve text,
rank evidence, resolve a question-time temporal scope, select a demonstration
question, or grant authority to a HAT or model.

## Fixed input boundary

The publication engine accepts only a verified Step 14 inventory bundle and a
verified Step 15 temporal/jurisdiction bundle. Their manifests, source-root
identity, source-tree digest, schema versions, and logical output digests are
rechecked before a candidate is processed. A checkpoint is bound to those
facts and cannot be reused with a changed corpus, a changed bundle, a changed
policy, or a different repository start commit.

The engine does not create a competing inventory or normalization model. It
uses the Step 14 source-registration candidate and the Step 15 document and
version identities. Unknown facts remain unknown; material Step 15 conflicts
remain review-required rather than becoming publication facts.

If more than one Step 14 object maps to one Step 15 version identity, Step 16
preserves every inventory ID and relative alias path. It does not make a
duplicate version canonical by default. It collapses aliases only when their
byte-bound derived metadata and policy facts agree. Any divergence is a
deterministic `DUPLICATE_VERSION_METADATA_CONFLICT`; the version remains
unpublished rather than allowing one copy to win.

## Trusted publication authority

Only repository-controlled application code can create a `PUBLISHED`
publication item. The German Law HAT supplies source-class and temporal policy
only. It cannot publish, approve, commit, change source state, write to S3,
override a quarantine, access Personal Memory, or select a question. Models
are not invoked and have no publication role.

The controlled CockroachDB validation uses the existing Step 9 state service
and Step 10 saga boundary. It is a disposable proof of state-transition,
idempotency, isolation, and append-only event behavior, not a production
database deployment.

## Eligibility

Each Step 15 version receives exactly one deterministic decision:

- `ELIGIBLE`
- `INELIGIBLE`
- `REVIEW_REQUIRED`
- `QUARANTINED`
- `CONFLICTING`
- `UNSUPPORTED`
- `ALREADY_PUBLISHED_EXACT_REPLAY`

Publication requires a stable Step 14 object identity and digest, a source
registration candidate, a clear document/version identity, clear and explicit
German federal scope for this release, allowed rights/privacy/redaction
classification, compatible German Law source class and authority assessment,
complete provenance, and accepted parser/validator coverage. Rights,
privacy, quarantine, authority, temporal, jurisdictional, identity, and
digest conflicts are preserved as exclusion reasons.

The currently supported release scope is official German federal consolidated
law. An official consolidation is retained as an authoritative secondary
reference, not converted into authentic promulgation. Court decisions,
legislative materials, guidance, commentary, private material, derived
summaries, and unknown sources retain their Step 13 classifications and are
not upgraded by repetition or filename similarity.

## Exact-byte and parser representations

For an eligible GII record the engine binds two distinct immutable S3 objects
through the existing Step 7 adapter:

1. The exact raw GII XML ZIP, with its Step 14 raw SHA-256 and byte length.
2. A deterministic UTF-8 textual projection assembled only from the
   hash-bound `law_record.json` and `provisions.jsonl` fields already present
   in the owned corpus.

The raw ZIP remains the exact source representation. The text projection is a
separate derived parser representation. It contains a deterministic header,
bounded provision labels, and the supplied official provision text; it does
not claim that the generic Step 11 parser understands ZIP or XML. The two
representations have separate snapshot identities, SHA-256 values, Object
Lock evidence, and provenance links.

The existing Step 7 adapter enforces deterministic keys, conditional
no-overwrite writes, version-specific read-back, checksums, SSE, Object Lock,
and retention. A typed bucket-capability receipt may be reused only for a
bounded same-run batch; every object still receives its own conditional write
and exact version-specific verification. No public ACL, delete, retention
bypass, bucket mutation, or unversioned snapshot is available.

## Parser, sections, and chunks

The projection is passed to the Step 11 generic plain-text parser and its
structural validator. Publication evidence records parser identity/version,
normalization profile, parsed-document identity, section and chunk manifests,
range coverage, warnings, findings, and quarantine status. A structural
failure, blocked security finding, unexplained gap, or unexplained overlap
blocks publication. Security findings are evidence only and do not execute
source content or establish intent.

The `german-law-publication-verification-1a.1` policy treats an explicitly
empty optional source label as absent, without changing the hash-bound raw
record. Non-empty malformed labels and unavailable provision text are
deterministic candidate exclusions. They are never normalized in place or
silently repaired.

## Temporal, jurisdiction, versions, and provenance

Step 16 consumes, rather than reinvents, Step 15 temporal and jurisdictional
records. It validates their accepted status for publication, preserves their
digests and limitations, and never substitutes operational capture,
retrieval, ingestion, verification, or filesystem times for legal effect.
It is not the later Step 21 question-time temporal resolver.

Each published item has a deterministic chain from external-volume/root
identity through Step 14 inventory object and raw digest, Step 9 candidate,
two exact snapshots, Step 11 parse output, Step 15 validation, authority and
rights decision, publication decision, and batch. Relative source path is
represented only by a digest in portable evidence. No raw corpus text,
absolute local path, credential, or presigned URL is committed.

## Batch, replay, and resources

The external Step 16 bundle is run-specific and contains canonical JSONL
decisions, published items, exclusions, snapshot bindings, provenance chains,
coverage, validations, conflicts, a gap report encoded in the summary, a
batch, checkpoint completion evidence, and a manifest. All files are written
with owned temporary files, `fsync`, no-overwrite atomic publication, and
read-back digest verification.

The resume spool is a local SQLite implementation detail below the approved
derived-data root. It has no semantic authority. It is bound to fixed input
digests and removed after finalization. A partially completed run can replay
an exact S3 key through the Step 7 adapter without creating a duplicate
version. A changed input is rejected, not merged.

Records are processed one at a time with bounded source reads and batches of
checkpoint commits. The implementation does not load corpus bodies or all
chunk content into memory and does not perform all-pairs comparison.

## Explicit non-goals

Step 16 does not implement retrieval, search, embeddings, vector indexes,
model routing, HAT routing, answer generation, Personal Memory, UI work,
OCR, corpus acquisition, publication of unsupported material, or Step 17.
It does not claim to sandbox trusted Python code. The trusted installation and
service boundaries are an allowlist, not an arbitrary-code sandbox.
