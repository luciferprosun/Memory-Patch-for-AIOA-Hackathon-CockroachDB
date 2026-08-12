# AOIA-Core Cross-Repository Provenance

AOIA-Core and this CockroachDB hackathon repository are separate repositories.
No AOIA-Core checkout was mutated or merged during this housekeeping task.

## Existing verified import

The initial repository commit imported a deliberately bounded knowledge-layer
history from AOIA-Core:

- 40 complete exact-byte snapshots;
- two provenance-marked excerpts from mixed whole-project documents;
- four source commits;
- zero redactions;
- 105 inspected but excluded candidates;
- 23 duplicate source-path instances intentionally omitted.

Start with:

- [Discovery method](../../docs/audits/DISCOVERY_METHOD.md)
- [Included files](../../docs/audits/INCLUDED_FILES.md)
- [Excluded candidates](../../docs/audits/EXCLUDED_CANDIDATES.md)
- [Potential gaps](../../docs/audits/POTENTIAL_GAPS.md)
- [Human-readable import manifest](../../docs/provenance/KNOWLEDGE_CHAT_IMPORT_MANIFEST.md)
- [CSV import manifest](../../docs/provenance/KNOWLEDGE_CHAT_IMPORT_MANIFEST.csv)
- [Imported historical artifacts](../../docs/history/2026-06-30_to_2026-07-24/)

## Reverification for this archive

The historical local checkout recorded by the original manifest was not
present at housekeeping time. The public remote
`https://github.com/luciferprosun/AOIA-Core.git` was therefore inspected
read-only. Its default branch was `main` at
`d7e3448afa4e33d58be8babbfcc615b13dff533f`; that main commit predates the
hackathon. The four imported source commits were each fetched by exact SHA and
remained reachable:

- `b7a3a1481ce382e516ed0d39e5ac334f3240c727`
- `24f3dc93b4528afe64e8edb6ecb3471899d59dbb`
- `33dfeb52263a50e23aa7edabdaab1fc47e60c9b9`
- `708fe063b3d81f1d61ca2cc7787f94550d52fbd0`

All 42 source blob hashes and all 42 archived target hashes matched the
existing import manifest. Nothing else was bulk-copied from AOIA-Core. See the
[source repository record](../manifest/source-repositories.json) for exact
refs and classifications.

## Originality labels

- AOIA knowledge-foundation artifacts: `PRE-EXISTING AOIA FOUNDATION` or
  `MODIFIED/CREATED IN AOIA-CORE DURING THE OFFICIAL WINDOW`, as recorded per
  manifest entry.
- CockroachDB repository implementation and its Steps 0A–43:
  `CREATED DURING COCKROACHDB HACKATHON`.
- AOIA-Core material is reference/provenance, not evidence that this
  repository created the AOIA platform itself.
