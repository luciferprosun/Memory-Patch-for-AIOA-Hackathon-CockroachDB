# German Law temporal and jurisdictional normalization 1A

## Boundary

Step 15 consumes one verified fixed Step-14 inventory manifest and emits typed temporal facts, jurisdiction facts, document/version identities, supersession candidates, conflicts, and review-only source-registry proposals. It normalizes recorded facts. It does not answer a legal question, select law for a person or time, publish a corpus version, or begin Step 16.

The Step-14 source tree remains logically immutable. Before planning, the normalizer verifies the Step-14 bundle, manifest and summary digests, source-root identity, and exact Step-14 tree fingerprint. It opens only Step-14-listed `law_record.json` files through `O_NOFOLLOW`, same-device, hash-bound reads. Pre/post `fstat` and raw SHA-256 prevent a changed file from being mixed into the snapshot. It never follows a corpus symlink, creates a corpus sidecar, or treats filesystem timestamps as legal time.

## Temporal model

The versioned `german-law-temporal-jurisdiction-normalization-1a` policy uses temporal-rule version `german-law-temporal-normalization-1a.1`. It preserves source field, normalized value, precision, timezone status, Step-14 object/candidate binding, evidence class, verification status, findings, and digest. Deterministic forms are ISO year/month/date, ISO timestamps with explicit or unknown timezone, the demonstrated German full-date form, and documented compact GII build timestamp. Unsupported forms become typed conflicts and are never guessed.

`PUBLISHED_AT`, `PROMULGATED_AT`, `ADOPTED_AT`, `EFFECTIVE_FROM`, `EFFECTIVE_TO`, `APPLICABLE_FROM`, `APPLICABLE_TO`, `DECISION_DATE`, `RETRIEVED_AT`, `INGESTED_AT`, `VERIFIED_AT`, and `SUPERSEDED_AT` remain separate. `EXECUTED_AT`, `REPEAL_DATE`, `CURRENTNESS_CHECKED_AT`, and `SOURCE_BUILD_AT` also remain separate: none is promoted to a stronger legal fact. Partial precision is retained; no month, day, midnight, or timezone is invented. The current clock is never substituted for missing legal metadata. Run timing is operational audit metadata and excluded from logical fact and version identity.

Intervals preserve the Step-13 half-open meaning where compatible explicit bounds exist. Reversed/equal prohibited bounds, incomparable precision or timezone, impossible supersession order, and verification-before-retrieval are deterministic conflicts. A conflict never rewrites an input fact.

## Jurisdiction and identity

The supported legal scopes are `DE_FEDERAL`, `DE_STATE`, and `EU`. State scope requires one canonical code: `DE-BW`, `DE-BY`, `DE-BE`, `DE-BB`, `DE-HB`, `DE-HH`, `DE-HE`, `DE-MV`, `DE-NI`, `DE-NW`, `DE-RP`, `DE-SL`, `DE-SN`, `DE-ST`, `DE-SH`, or `DE-TH`. Structured document metadata is preferred, then a unique typed Step-14 candidate. German language, a `.de` host, publisher name, state directory, or filename does not independently establish scope.

Filesystem object, raw content, normalized content, legal document, official identifier, publication, legal version, consolidation, applicability, and Step-9 candidate identities are separate. A version key binds immutable document/version markers and jurisdiction; its version identity adds raw and normalized digests, not operational timestamps. Exact duplicates keep every path provenance. Near duplicates create only `NEAR_DUPLICATE_REVIEW_ONLY`, never an equivalence, successor, or winner.

## Replay, artifacts, and Step 9

Input JSONL is streamed; source metadata is capped at 512 KiB; JSON depth, item count, relation count, and outputs are bounded. A Step-15-owned SQLite spool in the approved Step 8 derived-data namespace is a restart index, not legal authority or a CockroachDB replacement. Checkpoints bind policy, Git head, Step-14 manifest, source root/tree, and run identity. Changed source, incompatible checkpoint, collision, or digest mismatch fails closed.

Canonical artifacts are external and root-relative below `corpora/manifests/step15/<run-id>`: temporal/jurisdiction facts, document versions, supersession candidates, conflicts, review-only proposals, summary, completion checkpoint, and manifest. Writes are exclusive, no-overwrite, atomic, fsynced, and SHA-256 verified. Git receives no corpus body or absolute path.

`SourceRegistryNormalizationProposal` is permanently `automatic_update_allowed = false`. It is review evidence, not a registry mutation, verification escalation, or publication request. Step 9 remains the only source-registry/publication boundary. No migration is needed: the immutable external bundle is Step-15 evidence, and adding a temporal table would create competing canonical state before Step 16.

Controlled CockroachDB validation is loopback-only, in-memory v26.2.4. It replays existing migrations, proves proposal safety and Step-9 compatibility, exact/conflicting replay, RLS/FORCE RLS, zero publication, zero runtime DELETE, and graceful cleanup. It stores no corpus body or node store on the USB.

## Explicit deferrals

Step 15 does not implement Step 16 publication/verification, the Step 21 temporal resolver, retrieval, embeddings, vector indexes, model calls, OCR, downloads, web access, S3/AWS writes, HAT approval or commit authority, Personal Memory, a UI, a final question, or a Nachweisgesetz answer.
