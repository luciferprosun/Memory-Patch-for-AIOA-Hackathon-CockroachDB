# ADR-018: Generic parsing, normalization and chunking boundary

- Status: Accepted for Step 11 implementation; live closure evidence required
- Date: 2026-07-31
- Scope: Step 11, Generic Parsing, Normalization and Chunking Pipeline 1A

## Context

Step 10 established typed parser and validator receipts but deliberately used
synthetic validation ports. A production boundary must now transform exact
locked snapshot bytes into durable, replayable parsing evidence without
granting source content, a parser, a storage backend, a model, or a HAT any
authority.

The repository already owns snapshot identity and exact-version retrieval in
Step 7, source and publication policy in Step 9, orchestration in Step 10,
serializable retry and idempotency in Step 6, and tenant/user isolation in
Step 5. Step 11 must reuse those boundaries rather than create competing
storage, publication, or transaction mechanisms.

## Decision

Implement a deterministic, versioned registry with two exact profiles:

- `text/plain` through a strict UTF-8 plain-text parser;
- `application/json` through a strict RFC 8259-compatible canonical JSON
  parser.

All other media types fail closed. Markdown, PDF, OCR, Office, HTML, archives,
network acquisition, and executable content are not implemented.

### Canonical normalization

Accepted text uses the versioned NFC profile. It verifies the input digest and
length, permits and records one leading UTF-8 BOM, decodes strictly, rejects
NUL and unsafe controls, normalizes CRLF and CR to LF, applies NFC, enforces
bounds, and verifies idempotence.

NFKC, NFKD, case folding, accent stripping, punctuation replacement,
transliteration, whitespace collapse, and semantic rewriting are rejected as
profile behavior. Zero-width and bidirectional controls remain in the exact
normalized representation and generate findings.

### JSON projection

JSON rejects duplicate keys, non-finite numbers, malformed syntax, excessive
depth, excessive members, excessive arrays, and excessive strings. Arrays
preserve order and objects render with canonical key order. Scalars and empty
containers become sections with escaped JSON Pointers and retained value type.
No value is interpreted as executable input.

### Identity and offsets

Document, section, chunk, finding, quarantine, and artifact identities derive
from immutable canonical facts plus parser, normalization, chunking, ruleset,
and resource-policy versions. Exact replay is identical; a profile change is
distinguishable.

Every range is `[start, end)` in normalized NFC Unicode code-point positions.
Original byte ranges are absent when they cannot be proven. Stored section and
chunk text must equal the exact normalized range slice.

### Chunking

Use a model-neutral, section-local character profile with a 1024-character
maximum, 896-character target, 64-character overlap, and deterministic
boundary priority. No model tokenizer or cross-section chunk is used. Coverage
is complete and overlap is explicit.

### Untrusted-content evidence

Use a reviewable static ruleset without a model. Findings are categorized as
INFO, WARNING, or BLOCKING and contain only bounded excerpt hashes, not raw
unbounded evidence. A finding does not prove malicious intent and never
executes an instruction. Blocking evidence creates quarantine; quoted
educational examples receive explicit false-positive handling.

### Persistence

Reuse `knowledge_versions` and `knowledge_chunks`. Migration `0008` adds only
parsed documents, sections, and security findings. All three new tables have
RLS and FORCE RLS, least-privilege select/insert grants, no runtime delete,
and no `BYPASSRLS`.

Parse computation occurs outside SQL. The complete validated graph is inserted
in one Step 6 serializable transaction. Only SQLSTATE `40001` can retry that
callback. Exact replay returns existing identical evidence and conflicting
replay fails closed.

### Runtime integration

The concrete parser port uses Step 7 exact-version retrieval and returns
`synthetic_validation_boundary=false` only after complete persistence. The
concrete validator port deterministically verifies the persisted graph and
also returns `synthetic_validation_boundary=false`. Step 10 owns saga
milestones and Step 9 remains the publication boundary.

## Consequences

### Positive

- The same bytes and immutable profiles produce the same graph and receipts.
- Input text is preserved without compatibility or semantic rewriting.
- Unsupported formats cannot silently fall back to text.
- Prompt-injection-like content remains inert, reviewable data.
- Persistence is tenant-isolated, atomic, retry-safe, and replay-safe.
- Parser and validator receipts now represent real deterministic work.

### Costs and limitations

- JSON canonical output does not retain original source-byte offsets.
- Language remains unknown unless trusted typed metadata supplies a valid tag.
- The static ruleset can produce false positives and is evidence, not intent
  classification.
- Character chunking does not claim full grapheme segmentation or model token
  alignment.
- Additional formats require later pinned adapters and their own profiles.

## Rejected alternatives

- Treating unknown bytes as plain text.
- NFKC or NFKD compatibility normalization.
- Model-generated parsing, language inference, or injection classification.
- Ad-hoc regular-expression Markdown claiming CommonMark compatibility.
- Provider-tokenizer-dependent chunk identities.
- Parsing or S3 retrieval inside a retryable database transaction.
- A competing version, chunk, source, saga, or publication table.
- Persisting partial parse output after a child failure.
- Filling retrieval indexes or vector columns in Step 11.
- Allowing content or findings to approve publication or mutate policy.
