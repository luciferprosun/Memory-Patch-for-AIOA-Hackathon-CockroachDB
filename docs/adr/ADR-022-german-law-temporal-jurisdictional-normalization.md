# ADR-022: Preserve temporal and jurisdiction facts as reviewable evidence

- Status: accepted for Step 15 closure
- Date: 2026-08-01

## Context

Step 14 proves file-level provenance and source-registration candidates, but deliberately does not normalize legal dates, scope, version relationships, or conflicts. The German Law HAT temporal policy requires unknown dates and jurisdiction to remain unknown. The target machine has limited memory, and source material must remain immutable.

## Decision

Consume one digest-verified Step-14 snapshot and produce a canonical, external, restartable normalization bundle. Use strict bounded JSON metadata only when it is listed and raw-hash-bound by Step 14. Preserve fact type, partial precision, timezone status, source field, evidence class, and verification state. Preserve `CURRENTNESS_CHECKED_AT`, `SOURCE_BUILD_AT`, `EXECUTED_AT`, and `REPEAL_DATE` rather than converting them to stronger legal facts.

Normalize jurisdiction only from structured document metadata or a unique typed Step-14 candidate. Require a canonical state identity for `DE_STATE`. Use distinct document, version-key, and content/version identities. Record invalid intervals, contradictory scope, duplicate metadata conflicts, and near-duplicate lineage as review-required evidence.

Keep Step-15 output external and append-only through no-overwrite content-digest publication. Retain Step 9 as the source registry/publication boundary. A proposal is non-authoritative and cannot update the registry automatically. Do not add a migration: no new canonical database state is needed before Step 16, and a temporal registry table here would manufacture a competing authority boundary.

## Consequences

- No current clock, file mtime, language, URL, filename, or repetition can become a legal fact.
- Evidence remains traceable to Step 14 without committing raw corpus text.
- Exact replay and controlled checkpoint resume are deterministic and fail closed on changed input.
- The database validation proves compatibility but stores no corpus body.
- Step 16 owns publication and corpus verification; Step 21 owns question-time temporal resolution.

## Closure correction

The first full derived-only run revealed that a bounded optional descriptive
`version_basis` marker could be longer than the initial 512-character marker
limit. Treating that optional marker as a fatal metadata error wrongly omitted
otherwise hash-bound temporal facts for 29 records. The corrected temporal
rule version `german-law-temporal-normalization-1a.1` retains valid facts,
emits bounded `METADATA_FIELD_INVALID` review evidence for the optional marker,
and creates a separate no-overwrite external bundle. The earlier derived
bundle remains preserved and is not a closure artifact.

## Rejected alternatives

- Natural-language date extraction or an LLM would invent non-auditable legal facts.
- Mapping a currentness check, retrieval date, or mtime to legal effect would collapse required temporal distinctions.
- Treating a GII consolidation as authentic promulgation is invalid.
- Choosing a near-duplicate winner would destroy provenance and temporal uncertainty.
- Loading the full inventory/corpus into memory is unsafe on the target host.
- A new database table or automatic source update would preempt Step 16 and weaken Step 9.
