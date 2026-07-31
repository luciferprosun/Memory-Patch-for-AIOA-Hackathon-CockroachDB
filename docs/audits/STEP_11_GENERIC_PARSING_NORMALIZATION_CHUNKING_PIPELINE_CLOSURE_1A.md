# Memory Patch - Step 11 Generic Parsing, Normalization and Chunking Closure 1A

## Status

`IMPLEMENTATION AND ZERO-EXTERNAL-WRITE LIVE VALIDATION COMPLETE`

This record belongs to the intended single Step 11 closure commit. It becomes
completion evidence only when that commit is reachable on `origin/main`.

## Repository and scope

- authorized starting commit:
  `e9a4416e67c99718b47dac354c73fe393881be15`;
- branch `main`, with starting `HEAD == origin/main` and ahead/behind `0 0`;
- Steps 7 through 10 remained complete;
- Step 12 was not started;
- no PDF, OCR, Office, archive, Markdown, network, model, embedding, retrieval,
  vector-index, HAT-runtime, or domain-specific parser was introduced.

Step 11 implements a deterministic transformation and structural-validation
boundary. It does not grant semantic, publication, approval, or execution
authority.

## Standards and immutable profiles

The pipeline uses strict UTF-8, optional recorded leading UTF-8 BOM, LF line
endings, and Unicode NFC. It does not use NFKC/NFKD, case folding, accent
stripping, transliteration, punctuation replacement, or whitespace collapse.

The production registry supports exact `text/plain` and `application/json`
dispatch. JSON rejects duplicate members, non-finite numbers, malformed input,
and configured resource-limit violations. Arrays retain order and objects use
canonical stable key order. Markdown remains an unsupported future adapter.

The immutable profile identities validated live were:

- normalization: `unicode-nfc-text-normalization:1.0.0`;
- JSON parser: `generic-canonical-json-document-parser:1.0.0`;
- chunking: `model-neutral-character-chunking:1.0.0`;
- security rules: `prompt-injection-static-rules:1.0.0`.

Offsets are half-open normalized Unicode code-point ranges `[start, end)`.
Original byte offsets remain absent when line-ending conversion, NFC, or JSON
canonicalization prevents proof. JSON sections carry escaped structural
locators. Documents, sections, chunks, findings, receipts, and artifacts use
canonical immutable facts rather than clocks, sequences, or paths as identity.

## Parsing, chunking, and findings

Plain text is sectioned into exact blank-line-separated blocks. JSON is
projected into deterministic structural sections without ambiguous flattening.
The model-neutral chunker works within each section, uses a 1,024-character
maximum, 896-character target, 64-character overlap, deterministic boundary
priority, and safe adjacent cuts around combining/joiner characters.

The static prompt-injection rules emit bounded, digest-bound INFO, WARNING, or
BLOCKING findings. Findings preserve the source text, are not proof of intent,
never execute instructions, and grant no authority. Blocking findings produce
an explicit fail-closed quarantine decision; quarantined output cannot publish.

## Persistence and integration

Forward migration `0008_step11_generic_parsing_pipeline` adds:

- `memory_patch.parsed_documents`;
- `memory_patch.parsed_sections`;
- `memory_patch.parse_security_findings`.

It reuses `memory_patch.knowledge_versions` and
`memory_patch.knowledge_chunks`. It does not populate
`memory_patch.chunk_search_documents` and adds no vector object. The three new
tables have RLS and FORCE RLS, tenant/private-owner policies, append-only
runtime grants of SELECT/INSERT only, no `BYPASSRLS`, and no runtime DELETE.

The parsing service performs exact S3 retrieval, decoding, normalization,
sectioning, scanning, chunking, and structural validation outside database
transactions. One Step 6 serializable transaction atomically persists the
complete graph and retries only SQLSTATE `40001`. Exact replay returns the same
artifact; conflicting replay fails closed.

`GenericParsingPipelinePort` and `GenericParseArtifactValidatorPort` implement
the Step 10 ports. Their live ParseReceipt and ValidationReceipt both carry
`synthetic_validation_boundary=false`. Step 10 continues to own saga milestone
progression, and Step 9 remains the only legal publication transition boundary.

## Preserved failed attempts and repairs

All seven failed attempts remain separate, sanitized evidence. None was
rewritten as success:

1. initial migration command timeout;
2. shared validator timeout-cap mismatch;
3. lost structured database failure classification;
4. pre-reserved Step 10 knowledge-version digest conflict;
5. missing Step 9 source-to-knowledge-version prerequisite;
6. CLI/native Boolean and integer replay comparison conflict;
7. the 40-second drain bound was exhausted while the full `0001-0008` schema
   released leases.

Each attempt recorded zero S3 PutObject calls and zero external-volume writes,
used no force kill, closed its owned PID and ports, or deliberately preserved
the exact owned diagnostic directory when the cleanup invariant required it.
The final diagnostic directory was removed only after its ownership, stopped
PID, server-log digest, and preserved failure evidence were verified.

The last repair raises the bounded test-runtime minimum from 40 to 60 seconds.
The observed shutdown settings still total 25 seconds and retain a 15-second
scheduling cushion; `max(60 seconds, calculated bound)` remains capped at 120
seconds. The successful retry completed `node drain --self --shutdown`, emitted
the completion marker, exited the exact PID, closed all owned ports, removed
the owned temporary directory, and never used SIGKILL.

## Zero-external-write live validation

The frozen successful plan was bound to:

- worktree digest:
  `a9edd4f9e6e5e59734eb88c1dc9665ae7e0b1ff9621c68b88b1205f598fe77f5`;
- plan digest:
  `a71fc72b7ecadd433b3cf9cd720e2c8610fdc464f56924f9ea0d8b6ff26498df`;
- exact existing payload length: 92 bytes;
- exact payload SHA-256:
  `61088c464f21622d0dccd28d41e6f041c9bf7abf165542262c9ea7f8d51241ca`;
- exact existing S3 version ID: `kfDFfBsGlAR_KoQxDodzESlhebuYpAMx`.

The validation used a fresh loopback-only, in-memory CockroachDB v26.2.4
runtime, applied migrations `0001` through `0008`, and verified a checksum
replay that skipped all eight migrations. It reconciled the exact Step 10
external artifact and exact retained S3 version without writing either system.

Results:

- final Step 10 milestone: `PUBLISHED`;
- event chain: 8 transitions;
- publication chain: 3 events;
- parsed documents: 1;
- parsed sections: 3;
- knowledge versions: 1;
- knowledge chunks: 3;
- security findings: 0;
- `chunk_search_documents`: 0;
- exact replay: same completed saga and parse artifacts;
- conflicting replay: rejected with `IDEMPOTENCY_BINDING_CONFLICT`;
- new S3 writes: 0;
- new external-volume writes: 0;
- delete markers: 0;
- S3 version count after validation: 1;
- retention changes: 0;
- graceful drain: PASS;
- force kill: NO;
- persistent database or service: NO.

The canonical success evidence is
[`step11-parsing-pipeline-validation.json`](../evidence/parsing/step11-parsing-pipeline-validation.json).
Its internal evidence digest is
`4eac1d5c4bb88c477f10f35eac49e4121c3457c9671897741a32e1a0444b254a`
and its file SHA-256 is
`864414ced7e6dc7facd7e83adf5f9b7b3b53667339445c4c01408556cfd78b04`.

## Validation and security review

- compile/import validation: PASS;
- migration manifest and offline validation: PASS, 8 migrations;
- fresh application and checksum replay: PASS;
- targeted parser, persistence, integration, cleanup, and documentation tests:
  PASS;
- Step 4 through Step 10 regressions: PASS;
- full repository suite: 879 PASS, greater than the 706-test Step 10 baseline;
- canonical contract validation: PASS;
- secret and suspicious-string review: PASS with documented/prohibited
  occurrences only;
- `git diff --check`: PASS;
- import-time external I/O: none.

No credential, SSO cache, account identifier, raw device UUID, raw mount path,
or personal/German-law content is present in committed evidence. No S3 write,
retention mutation, delete API, shell/network content execution, publication
bypass, RLS weakening, or Step 12 implementation was introduced.

## Closure boundary

Step 7 remains complete. Step 8 remains complete. Step 9 remains complete.
Step 10 remains complete. Step 11 becomes complete only when this intended
commit is pushed and verified on `origin/main`. Step 12 remains not started.
