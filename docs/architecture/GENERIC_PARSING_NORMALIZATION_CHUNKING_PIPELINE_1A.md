# Generic Parsing, Normalization and Chunking Pipeline 1A

## Purpose

Step 11 turns one exact locked snapshot version into deterministic parsed
evidence. It implements the concrete production `ParserPort` and structural
`ValidatorPort` introduced by Step 10. It is domain-neutral and does not add a
HAT runtime, retrieval, embeddings, models, German-law rules, or publication
authority.

The pipeline is:

```text
exact locked S3 version
  -> strict media dispatch and decoding
  -> versioned canonical normalization
  -> parsed document and sections
  -> static untrusted-content findings
  -> deterministic section-local chunks
  -> structural validation
  -> one atomic CockroachDB parse graph
  -> real ParseReceipt and ValidationReceipt
```

S3 retrieval, parsing, scanning, chunking, and validation happen outside a
database transaction. Only the already-computed complete graph is persisted
inside one bounded Step 6 serializable transaction.

## Standards baseline

- Unicode normalization follows [Unicode Standard Annex 15](https://www.unicode.org/reports/tr15/)
  and uses NFC.
- JSON acceptance follows [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259)
  with additional fail-closed bounds.
- Optional language metadata uses a typed BCP 47-style boundary informed by
  [RFC 5646](https://www.rfc-editor.org/rfc/rfc5646). Unknown language is
  `None`; no language is invented.
- [OWASP prompt-injection guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
  informs the threat model. Static findings remain security evidence, not a
  statement of intent or semantic truth.
- [CommonMark 0.31.2](https://spec.commonmark.org/0.31.2/) is not claimed.
  `text/markdown` is unsupported in 1A because no pinned CommonMark parser is
  introduced.

Compatibility normalization is deliberately forbidden. NFKC and NFKD can
erase distinctions in quoted, scientific, technical, or legal material.

## Authority model

| Component | Step 11 role |
| --- | --- |
| Memory Patch kernel | Semantic authority |
| Step 9 source service | Eligibility and publication transition boundary |
| Step 10 saga | Durable orchestration authority |
| CockroachDB parse tables | Immutable transformation evidence only |
| S3 | Exact-version storage evidence only |
| External volume | Derived staging evidence only |
| Parser | Deterministic transformation only |
| Structural validator | Deterministic consistency check only |
| Security findings | Review and quarantine evidence only |
| Models and HATs | No authority and no execution role |

Source content is always data. It cannot invoke tools, select paths, access the
network, execute templates or commands, mutate policy, approve publication, or
update memory.

## Parser registry

`ParserRegistry` dispatches only an exact normalized media type. Parameters,
aliases, suffix guessing, filename guessing, and arbitrary text fallback are
not accepted.

| Exact media type | Immutable profile | Status |
| --- | --- | --- |
| `text/plain` | `generic-utf8-plain-text-parser:1.0.0` | Supported |
| `application/json` | `generic-canonical-json-document-parser:1.0.0` | Supported |
| `text/markdown` | none | Unsupported |
| PDF, HTML, Office, archives, images | none | Unsupported |

Unsupported media fails with `UNSUPPORTED_MEDIA_TYPE`. The registry performs
no filesystem, process, AWS, database, or network access during import or
dispatch.

## Normalization profile

The profile is `unicode-nfc-text-normalization:1.0.0`:

1. verify exact byte length and SHA-256;
2. remove at most one leading UTF-8 BOM and record its presence;
3. decode UTF-8 strictly;
4. reject NUL and prohibited C0/C1 control characters while allowing tab and
   line feed;
5. convert CRLF and lone CR to LF;
6. normalize to Unicode NFC;
7. verify all resource limits;
8. hash the normalized content;
9. verify NFC idempotence.

The profile preserves document boundaries, leading and trailing text, final
newline state, blank lines, tabs, meaningful spaces, punctuation, accents,
case, zero-width characters, and bidirectional controls. It does not fold
case, strip accents, transliterate, replace quotes, collapse whitespace, or
perform semantic rewriting. Zero-width and bidirectional controls are
preserved and surfaced as findings.

## Plain-text projection

Blank-line-separated non-empty blocks become `TEXT_BLOCK` sections. A line
containing only spaces or tabs is a separator. Separators remain in the
normalized document but are outside section ranges. Section order is document
order; empty sections are not created and headings are not inferred.

## JSON profile

The JSON parser:

- requires strict UTF-8 after the documented optional leading BOM;
- rejects malformed syntax and duplicate object members;
- rejects `NaN`, positive infinity, and negative infinity;
- preserves booleans, numbers, strings, null, arrays, and objects as distinct
  JSON types;
- preserves array order;
- normalizes JSON keys and strings through the same NFC and line-ending
  policy;
- rejects keys that collide after normalization;
- renders canonical output with sorted object keys, compact separators, and
  UTF-8 Unicode characters;
- creates one section for each scalar or empty container;
- binds every JSON section to a correctly escaped JSON Pointer;
- never evaluates a key or value as code, a URL, a path, or an instruction.

Empty arrays, empty objects, false, zero, empty strings, and null retain
different canonical renderings and section identities. Original byte offsets
are absent because canonical rendering cannot prove a source-byte range.

## Parsed models and identity

All public parse models are frozen typed dataclasses. Document, section,
chunk, finding, quarantine, parse-artifact, and validation identities derive
from canonical immutable facts. Random IDs, timestamps, sequence values, and
local filesystem paths are not semantic identity.

Profile digests bind parser, normalization, chunking, security rules, and
resource-policy versions. Changing one version therefore creates a distinct
document and artifact identity. Exact replay creates identical identities and
digests.

### Offset basis

All stored section, chunk, and finding ranges are half-open `[start, end)`
ranges in normalized Unicode code-point positions in the NFC document text:

```text
NORMALIZED_UNICODE_CODE_POINTS_NFC
```

For every section and chunk, the range slice must equal the stored content.
The code does not call these byte offsets and does not fabricate original byte
ranges after decoding, line-ending conversion, NFC, or JSON rendering.

## Chunking profile

The profile is `model-neutral-character-chunking:1.0.0`:

| Property | Value |
| --- | ---: |
| Maximum characters | 1024 |
| Target characters | 896 |
| Minimum characters | 1 |
| Overlap | 64 characters |
| Boundary search window | 160 characters |
| Cross-section chunks | No |

Boundary priority is section end, line break, sentence punctuation followed
by whitespace, whitespace, then a bounded hard cut. Every section is chunked
independently. Coverage cannot contain a gap. Overlap is explicit and bounded.
A safe adjacent cut is preferred over splitting before a combining mark,
variation selector, or across a zero-width-joiner boundary. This is not a
claim of complete Unicode grapheme segmentation.

Chunk identity binds tenant, source, HAT scope, knowledge version, document,
section, global and section ordinals, exact range, content digest, chunking
profile, overlap, optional language tag, and offset basis. No model tokenizer
is used.

## Static prompt-injection findings

The ruleset is `prompt-injection-static-rules:1.0.0`. It deterministically
records these categories:

- `ROLE_OR_SYSTEM_INSTRUCTION_MARKER`;
- `INSTRUCTION_OVERRIDE_PHRASE`;
- `TOOL_OR_COMMAND_EXECUTION_REQUEST`;
- `SECRET_OR_CREDENTIAL_EXFILTRATION_REQUEST`;
- `REMOTE_OR_INDIRECT_INSTRUCTION`;
- `HIDDEN_MARKUP_OR_COMMENT_INSTRUCTION`;
- `ENCODED_OR_OBFUSCATED_INSTRUCTION_SIGNAL`;
- `ZERO_WIDTH_OR_BIDI_CONTROL_SIGNAL`;
- `RAG_POISONING_OR_RETRIEVAL_MANIPULATION_SIGNAL`.

Each finding contains a deterministic identity, rule and ruleset version,
category, severity, normalized range, optional section, a bounded surrounding
excerpt digest, recommended action, and finding digest. Raw excerpts are not
persisted. Encoded-looking material is flagged without decoding it.

The severity policy is `INFO`, `WARNING`, or `BLOCKING`. Quoted educational or
security examples downgrade matching blocking phrases to informational
evidence. Warning and informational findings never rewrite content. A
blocking finding creates an explicit quarantine decision and prevents parse
persistence, PARSED, VALIDATED, and publication progression.

## Resource policy

`generic-parsing-resource-limits:1.0.0` sets explicit bounds:

| Resource | Maximum |
| --- | ---: |
| Input bytes | 64 MiB |
| Decoded or canonical characters | 8 Mi characters |
| JSON depth and recursion depth | 64 |
| JSON object members | 100,000 |
| JSON array length | 100,000 |
| Individual string | 1 Mi character |
| Sections | 100,000 |
| Section length | 4 Mi characters |
| Chunks | 100,000 |
| Chunk length | 1,024 characters |
| Security findings | 1,024 |
| Metadata | 16 KiB per value |

Limit failures are typed and do not silently truncate. The implementation
does not decompress archives, perform unbounded recursion, use a tokenizer,
or call an external service.

## Structural validation and quarantine

`ParseArtifactValidator` verifies document hashes and manifests, section and
chunk order, ranges, slice equality, content digests, section-local chunk
binding, chunk coverage, finding ranges, quarantine consistency, and profile
identities. The production validator port additionally verifies the exact
Step 10 receipt, saga, snapshot, source, S3 version, and durable graph.

Integrity, identity, range, coverage, persistence, or receipt conflicts fail
closed with typed reasons. A failed parser never reaches `PARSED`; a failed
validator never reaches `VALIDATED`; a quarantined saga cannot publish. There
is no casual unquarantine switch and no evidence deletion.

## Persistence and isolation

Migration `0008_step11_generic_parsing_pipeline` reuses
`memory_patch.knowledge_versions` and `memory_patch.knowledge_chunks` and adds:

- `memory_patch.parsed_documents`;
- `memory_patch.parsed_sections`;
- `memory_patch.parse_security_findings`.

RLS and FORCE RLS apply to all three tables through the established Step 5
HAT-scope context. Runtime access is `SELECT, INSERT` only. There is no runtime
update or delete grant and no `BYPASSRLS`. Findings are append-only.

The service uses the Step 6 transaction runner and durable idempotency record.
Only SQLSTATE `40001` retries the transaction callback. One transaction
verifies or inserts the knowledge-version reservation and inserts the parsed
document, sections, findings, and chunks. Any child failure rolls back the
whole graph. Exact replay returns the same graph; conflicting replay fails
closed.

`memory_patch.chunk_search_documents` is not populated. Full-text and vector
retrieval remain later roadmap work.

## Step 10 and Step 9 integration

`GenericParsingPipelinePort` retrieves the exact S3 version through the Step 7
storage contract, verifies payload and storage evidence, runs the pure parser,
persists the complete graph, and returns a `ParseReceipt` with
`synthetic_validation_boundary=false`.

`GenericParseArtifactValidatorPort` loads and verifies the complete graph and
returns a `ValidationReceipt` with
`synthetic_validation_boundary=false`. It performs structural validation, not
semantic or model judgment.

Step 10 continues to own milestone progression. Step 9 remains the only legal
publication boundary. Neither adapter directly updates publication state.

## Limitations and deferred work

- Markdown remains unsupported until a pinned conforming adapter is justified.
- PDF, OCR, Office, HTML, archives, images, and network acquisition are out of
  scope.
- Language is not inferred.
- Complete grapheme-cluster segmentation is not claimed.
- Search documents, indexes, embeddings, retrieval, ranking, and Step 12 HAT
  runtime remain unopened.
