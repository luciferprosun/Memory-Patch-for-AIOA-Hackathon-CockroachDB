# Memory Patch for AIOA — CockroachDB Hackathon Jury Archive

This directory is the judge-facing map of the completed Memory Patch for AIOA
CockroachDB hackathon repository. Start with the [master index](INDEX.md), then
use the [timeline](TIMELINE.md) to follow the engineering progression.

## Historical Step43 snapshot

This archive and its artifact manifest are an immutable historical snapshot at
the Step43 cutoff, not an inventory of the later final submission tree. Work
continued during the hackathon after Step43, including the bounded jury
runtime, Cognito access, AWS public deployment, CockroachDB v26.2.5 final
environment, ccloud release gate, and documentation closure. That continuation
is recorded separately in the
[final hackathon release manifest](../docs/submission/FINAL_HACKATHON_RELEASE_MANIFEST.md).

The historical snapshot and current final submission are intentionally
different states. Historical Git blobs remain available and verifiable; later
changes to current files do not invalidate the pinned Step43 evidence.

Memory Patch separates a model's answer from evidence and authority. It routes
a question to a bounded knowledge domain, retrieves independently governed
evidence, resolves temporal and source constraints, builds a deterministic
Correction Packet, verifies the corrected answer, and fails closed when the
answer cannot be verified. A correction can enter private Personal Memory only
after explicit owner approval; that memory never becomes canonical evidence.

## Date and provenance boundaries

- Official CockroachDB × AWS Hackathon window: `2026-06-30 10:00` through
  `2026-08-18 17:00` America/New_York (`2026-06-30T14:00:00Z` through
  `2026-08-18T21:00:00Z`).
- First attributable project activity observed: AOIA-Core knowledge-foundation
  commit `b7a3a1481ce382e516ed0d39e5ac334f3240c727` at
  `2026-07-15T03:59:22+02:00` (`2026-07-15T01:59:22Z`).
- First commit in this CockroachDB repository: `04709a84f8cb8407a6fdf060210403f8e323133f`
  at `2026-07-24T08:59:40+02:00`.
- Step43 cutoff used by this historical archive:
  `b9dda5eba15aea41edeb8498c4fe524037bd0a07` at
  `2026-08-13T00:16:17+02:00` (`2026-08-12T22:16:17Z`).

The official window and the observed work window are intentionally reported
separately. The archive does not backdate work or present pre-existing AOIA
material as work created in this repository.

## What the hackathon repository contains

- The canonical roadmap is complete from Step 0A through Step 43.
- CockroachDB is used for durable lineage, scoped retrieval, RLS/FORCE RLS,
  idempotent workflows, Personal Memory lifecycle state, audit, review, and
  tested backup/restore.
- The German Law Golden Path includes real source/version lineage, retrieval,
  temporal resolution, evidence-blind Draft V1, correction, verification, and
  fail-closed output.
- Steps 36 and 41 prove credential and security boundaries.
- Step 42 freezes and restores the release candidate in an isolated drill.
- Step 43 provides the final live/replay demo and submission package.

## Where to look

1. [Final project README](../README.md)
2. [Post-Step43 final release manifest](../docs/submission/FINAL_HACKATHON_RELEASE_MANIFEST.md)
3. [Roadmap 0A–43](../docs/roadmap/PRODUCTION_ROADMAP.md)
4. [System overview](../docs/architecture/MEMORY_PATCH_SYSTEM_OVERVIEW_1A.md)
5. [German Law Golden Path](../docs/demo/MEMORY_PATCH_DEMO_GOLDEN_PATH_1A.md)
6. [Step38 full E2E evidence](../docs/evidence/e2e/step38-german-law-full-e2e-validation.json)
7. [Step41 security evidence](../docs/evidence/security/step41-full-security-regression-validation.json)
8. [Step42 RC restore evidence](../docs/evidence/release/step42-rc-backup-restore-validation.json)
9. [Step43 submission package](../docs/submission/HACKATHON_SUBMISSION_PACKAGE_1A.md)
10. [Historical deterministic artifact manifest](manifest/artifact-manifest.json)

## AOIA-Core separation

AOIA-Core is a separate repository and is not merged here. Forty exact-byte
source snapshots and two provenance-marked excerpts had already been imported
from four AOIA-Core commits. This archive links to those records under
[AOIA-Core references](06_AOIA_CORE_REFERENCES/README.md). All four source
commits and all 42 source/copy hashes were reverified read-only against the
public AOIA-Core remote for this curation.

## Integrity method

Canonical in-repository material is linked rather than duplicated. Three
bounded external prompt/reference files are copied exact-byte after secret
screening. Every curated source artifact has a SHA-256, size, provenance basis,
classification, source path, and source commit where available in the
historical [artifact manifest](manifest/artifact-manifest.json). Its 302 paths
and 257 Git-backed hashes retain their Step43 meaning. The exact manifest bytes
are bound by [artifact-manifest.sha256](manifest/artifact-manifest.sha256);
neither file is regenerated for the final repository state.

This is repository engineering evidence, not an external security, legal, HA,
or disaster-recovery certification.
