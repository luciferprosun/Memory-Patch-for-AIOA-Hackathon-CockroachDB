# German Law corpus inventory, deduplication, and source registration 1A

## Boundary

Step 14 inventories an operator-owned corpus without changing it. It produces
portable evidence about observed bytes and deterministic candidates for the
existing Step 9 source registry. It does not publish sources, decide legal
meaning, infer missing legal dates, normalize jurisdictional history, answer a
legal question, or begin Step 15.

The source tree is logically immutable. The scanner uses `lstat`, stable path
ordering, same-device checks, `O_NOFOLLOW`, pre/post `fstat`, streaming
SHA-256, and a second source-tree fingerprint. Symlinks and special or
unreadable objects become records; they are never followed or silently
omitted. Temporary files, checkpoints, and generated output are confined to
the Step 8 `corpora/manifests` operation root.

## Typed inventory

The versioned contract is `german-law-corpus-inventory-1a`, schema `1.0.0`.
Immutable models cover the run, file observations, path aliases, exact and
normalized duplicate groups, near-duplicate candidates, license and privacy
assessments, logical quarantine, Step 9 registration candidates, summary, and
bundle manifest.

Portable identities bind canonical immutable facts:

- raw bytes use a streaming raw SHA-256 and byte length;
- a file record binds source-root identity, root-relative POSIX path digest,
  file metadata, classification digests, and content digest;
- path and content identity remain separate;
- timestamps are descriptive and never become content or legal-time identity;
- absolute paths, excerpts, credentials, and raw personal data are excluded
  from portable records.

Unknown jurisdiction, legal status, license, privacy, publisher, version, or
effective time remains unknown. German language and directory names do not
create legal jurisdiction or authenticity.

## Bounded scan and replay

The scanner reads in bounded chunks, keeps a bounded number of handles open,
and uses one Step-14-owned SQLite spool outside the source tree for restart
state and indexed grouping. The spool is an implementation aid with no
semantic or transactional authority. Atomic checkpoints bind policy digest,
source-root identity, tree snapshot, and completed-object identities. A resume
fails closed if any bound fact changes. After successful canonical output, the
spool and partial files are removed.

Canonical JSON and JSONL use stable ordering. Output uses exclusive creation,
same-directory temporary files, `fsync`, atomic rename, read-back digest
verification, and no overwrite. Exact replay verifies the existing completed
bundle rather than creating a second logical result.

## Duplicate evidence

Exact duplicate groups require both identical verified SHA-256 and byte
length. Every path alias remains in evidence. Reclaimable bytes are an
informational metric only; Step 14 performs no delete, move, rename, hardlink,
or canonical-winner selection.

Normalized duplicate groups are permitted only for Step 11-supported strict
`text/plain` and `application/json` profiles. Raw and normalized digests stay
distinct. PDF, archives, HTML, XML, Office, databases, and unknown binary
formats never receive invented normalized equivalence.

Near duplicates are deterministic review candidates under
`bounded-minhash-shingle-candidates-1a`. Candidate generation uses bounded
buckets and versioned token/shingle fingerprints instead of an all-pairs
scan. Similarity does not prove legal equivalence. Authority-class and
temporal/version conflicts are preserved, and no winner is selected.

## Rights, privacy, and quarantine

License status is evidence-bound: `VERIFIED_ALLOWED`,
`VERIFIED_RESTRICTED`, `DECLARED`, `UNKNOWN`, or `CONFLICTING`. Public access
or an official publisher alone does not establish redistribution rights.
Privacy status is `PUBLIC`, `PRIVATE`, `POTENTIALLY_SENSITIVE`, `UNKNOWN`, or
`CONFLICTING`. Deterministic signals retain rule IDs, counts, and location
digests, never source excerpts.

Quarantine is logical metadata only: `CLEAR`, `REVIEW_REQUIRED`, or
`QUARANTINED`, with typed reasons. It cannot move, rewrite, delete, approve, or
publish an object.

## Step 9 and Step 13 integration

`build_source_registry_record` maps evidence-backed German federal official
consolidations into the existing Step 9 `SourceRegistryRecord`. The record
binds the Step 13 source class, exact candidate and alias-set digests,
jurisdiction, scope, authority assessment, license, access, parser and
transformation identities, and exact inventory artifact digest. Official
consolidation remains distinct from authentic promulgation.

Every registration begins at Step 9 `REGISTERED` genesis and uses a
deterministic Step 6 operation/idempotency identity. Exact replay returns the
same logical record; a different digest under the same source identity fails
closed. Review and quarantine use Step 9 transitions. No Step 14 code can
write `PUBLISHED`, bypass the Step 9 service boundary, elevate a HAT, or grant
model authority.

Controlled validation uses migrations `0001` through `0009` in one
loopback-only, in-memory CockroachDB v26.2.4 runtime. It stores registration
metadata only, never corpus bodies, and proves RLS/FORCE RLS, no runtime
DELETE, replay, conflict rejection, zero publication, cross-tenant isolation,
and graceful cleanup without a persistent database.

## Explicit deferrals

Step 14 does not implement temporal or jurisdictional normalization,
publication, retrieval, embeddings, OCR, downloads, model calls, legal answer
generation, or a final demonstration question. Those boundaries remain with
later roadmap steps.
