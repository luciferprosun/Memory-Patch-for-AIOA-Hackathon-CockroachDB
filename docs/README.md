# AIOA Memory Patch / Knowledge Chat provenance import

This repository isolates the Memory Patch and Knowledge Chat layer of AIOA for
the CockroachDB hackathon. The Critical Loop is a separate AIOA layer and is
intentionally outside this submission.

This import contains reports, plans, inventories and validation evidence
created or substantively updated from June 30, 2026 through July 24, 2026.
Importing historical reports does not mean that a final CockroachDB
implementation already exists. The new hackathon implementation will be built
separately in this repository.

Raw German-law datasets, raw UNIX corpus records, retrieval-index data,
application code and provider secrets are intentionally excluded. Every
imported document has source commit, ref, date and SHA-256 provenance.

## Start here

- [External data volume contract](EXTERNAL_DATA_VOLUME.md)
- [Knowledge Module Migration Handoff 1B](history/2026-06-30_to_2026-07-24/plans/KNOWLEDGE_MODULE_MIGRATION_HANDOFF_1B.md)
- [Provider-Independent Knowledge Context Bridge 1A](history/2026-06-30_to_2026-07-24/memory-and-retrieval/PROVIDER_INDEPENDENT_KNOWLEDGE_CONTEXT_BRIDGE_1A.md)
- [German Federal Law Knowledge HAT integration map](history/2026-06-30_to_2026-07-24/german-law-hat/KNOWLEDGE_HAT_INTEGRATION_MAP.md)
- [README Knowledge and Memory Layer excerpt](history/2026-06-30_to_2026-07-24/mixed-document-excerpts/README_KNOWLEDGE_SECTIONS.md)
- [Architect handoff Knowledge sections](history/2026-06-30_to_2026-07-24/mixed-document-excerpts/START_HERE_ARCHITECT_KNOWLEDGE_SECTIONS.md)

## Provenance and audit

- [Human-readable import manifest](provenance/KNOWLEDGE_CHAT_IMPORT_MANIFEST.md)
- [CSV import manifest](provenance/KNOWLEDGE_CHAT_IMPORT_MANIFEST.csv)
- [Source repository snapshot](provenance/SOURCE_REPOSITORY_SNAPSHOT.md)
- [Discovery method](audits/DISCOVERY_METHOD.md)
- [Included files](audits/INCLUDED_FILES.md)
- [Excluded candidates](audits/EXCLUDED_CANDIDATES.md)
- [Potential gaps](audits/POTENTIAL_GAPS.md)

The manifest is authoritative for the 42 imported records: 40 complete copies
and two clearly marked partial excerpts.
