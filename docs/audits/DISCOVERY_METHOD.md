# Discovery Method

## Scope and authority

Discovery was read-only against `/home/l/AOIA_PRODUCTION/repos/AOIA-Core`. Git history was the primary date
authority. No checkout, reset, clean, stash, source edit, source artifact
regeneration or source commit occurred.

The inclusive Europe/Berlin window was:

- Start: `2026-06-30T00:00:00+02:00`
- End: `2026-07-24T23:59:59+02:00`

## Procedure

1. Captured source status, branch, HEAD and remotes before discovery.
2. Verified GitHub authentication and the exact public empty target repository.
3. Refreshed source refs with `git fetch --all --prune` without changing the
   checked-out branch or worktree.
4. Enumerated all 390 reachable commits and 66 local/remote refs.
5. Selected all 57 unique commits whose Git commit dates fell inside the exact
   window.
6. Enumerated Markdown, text, JSON, YAML, TOML, HTML, PDF-metadata and related
   documentation changes, including parent blobs for deletion/rename checks.
7. Scanned current tracked documentation by content and searched filenames and
   commit diffs for Knowledge Chat, Knowledge HAT, memory, ingestion, corpus,
   retrieval, RAG/vector/embedding concepts, provenance, German law,
   Nachweisgesetz and Kimi terms.
8. Reviewed 212 documentation-format change events representing 147 unique
   paths. Eighty-four unique paths had direct keyword hits; the remaining paths
   from relevant commits were inspected for contextual or mixed relevance.
9. Inspected the July 15 UNIX Knowledge HAT artifact family by structure and
   SHA-256, separating inventories/reports/manifests from 13 raw normalized
   records, the raw retrieval index and generated demo output.
10. Reviewed history of older Memory Layer doctrine to distinguish substantive
    work from the July 15 branding-only edit.
11. Checked all in-window name-status history for relevant renamed/deleted
    documentation. No eligible relevant deletion or rename was found.
12. Checked source status for untracked documentation. None was present.

## Classification

- Category A: complete source document copied byte-for-byte.
- Category B: mixed document represented by a provenance-marked excerpt whose
  selected source sections are verbatim.
- Category C: incidental mention; excluded and listed in the exclusion audit.

Exact duplicate content was grouped by SHA-256. Twenty-three redundant path
instances were omitted. The canonical r1 validation record was preferred where
an identical 1A/r1 copy existed. Distinct superseded 1A provenance records were
retained when their bytes and historical identity differed.

## Counts

- Candidate documents inspected: 147
- Complete documents imported: 40
- Mixed-document excerpts: 2
- Excluded candidates: 105
- Exact duplicate path instances omitted: 23
- Redactions: 0
