# ADR-023: German Law HAT publication and corpus-verification boundary

## Status

Proposed. It becomes accepted only with the completed Step 16 validation and
closure commit.

## Context

Steps 14 and 15 establish an immutable inventory/registration candidate set
and typed temporal/jurisdiction normalization. Later retrieval must consume a
published, exact-byte, provenance-bound set rather than treat a local corpus
path or a repeated document as authority.

The owned corpus includes raw GII XML ZIPs and pre-existing normalized
metadata/provision artifacts. The generic Step 11 parser supports strict
plain text and JSON, not ZIP or XML. S3 snapshot authority and the Step 9/10
publication state boundary already exist and must not be duplicated.

## Decision

1. Step 16 consumes only verified Step 14 and Step 15 manifests and fails
   closed on any identity, schema, digest, source-tree, or checkpoint change.
2. A candidate needs deterministic eligibility evidence before publication;
   unclear rights, privacy, quarantine, authority, identity, temporal, or
   jurisdiction facts remain excluded or review-required.
3. Each eligible current GII version has two distinct S3 bindings: exact raw
   ZIP bytes and a deterministic, provenance-bound text projection. The
   projection is a derived parser input, not a claim of native ZIP support.
4. All S3 interaction uses the existing Step 7 Object-Lock adapter. A typed,
   matching bucket-capability receipt can be reused for a bounded batch, while
   every object remains conditionally written and version-specifically
   verified.
5. Step 11 parses and validates the text projection. Coverage and security
   evidence are prerequisites, not advisory metadata, for body publication.
6. Step 15 temporal/jurisdiction facts and conflicts are consumed unchanged.
   Step 16 validates them for publication but does not resolve legal time for
   a user question.
7. The external publication bundle is the full-corpus durable validation
   output. A disposable CockroachDB runtime proves the existing Step 9/10
   trusted state-machine integration and is gracefully removed afterward.
8. Multiple inventory aliases for one legal version are retained in output
   provenance. Step 16 only treats aliases as equivalent when all
   byte-bound metadata and policy facts agree; divergent alias metadata is a
   blocking conflict, not a deterministic winner-selection rule.
9. Policy version `german-law-publication-verification-1a.1` distinguishes a
   declared empty optional label from malformed non-empty source metadata.
   Empty labels remain absent in the derived projection; malformed fields and
   unavailable body text are excluded without rewriting the source.

## Consequences

The corpus becomes ready for later retrieval only within its published,
scoped, source-class, parser-covered, and provenance-bound limits. The system
does not claim completeness or general legal-answer capability. Unsupported
or ambiguous assets remain visible in the corpus-gap report.

S3 object count is intentionally greater than legal-version count because raw
source bytes and parser representation must remain distinct. The design trades
storage and runtime for reproducibility, exact replay, and accurate parser
provenance.

## Rejected alternatives

### Treat ZIP as parser input

Rejected because Step 11 does not support ZIP/XML and pretending otherwise
would create false parser coverage.

### Parse local normalized files without an immutable snapshot

Rejected because later evidence could not bind parser output to exact bytes
under the Step 7/10 authority model.

### Publish on source name, language, directory, or repetition

Rejected because these are not sufficient authority, jurisdiction, rights, or
identity evidence.

### Add a separate Step 16 registry or publication state machine

Rejected because Step 9/10 already provide the durable idempotency, event, and
publication transition contracts.

### Use a model to extract missing legal facts or decide eligibility

Rejected because a model cannot create legal temporal, jurisdictional, rights,
or publication authority.

### Store the entire corpus or chunk body in CockroachDB validation

Rejected because the controlled database validates state behavior only; the
full corpus output belongs in the external canonical artifact bundle and exact
S3 snapshots.
