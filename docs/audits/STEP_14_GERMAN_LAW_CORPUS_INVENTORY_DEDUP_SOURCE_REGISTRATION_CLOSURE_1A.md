# Step 14 — German Law Corpus Inventory, Deduplication and Source Registration 1A

## Verdict

`COMPLETE AND PUSHED at actual closure commit` once this record is reachable on
`origin/main`. Step 15: NOT STARTED.

## Step 13 closure audit

The Step 13 German Law HAT boundary was audited before Step 14. One material
court-ranking scope defect was corrected and pushed in commit
`8ca5e70e7379653879bfe077721d122a62741aba`. Targeted Step 9–13 regressions
passed 375 tests, the then-current full suite passed 1,025 tests, migrations
`0001`–`0009` applied and replayed, and the disposable CockroachDB runtime
drained without force kill. No corpus operation was included in that repair.

## Controlled corpus inventory

The recovered source library was verified on the approved USB under the
sanitized device reference
`external-volume-sha256:f6aff52dd4102add848e14b8037edb14d6fab427b6b319c5cf3a2cca4e562c20`.
Its portable root identity is
`5c149c3e2f3fd7a90f5c416d659dc0e36f27873d7eac9dba24f0c81830747d06`.
The deterministic read-only scan observed:

- 6,247 directories including the root;
- 19,391 stable files;
- 1,963,389,627 bytes;
- 19,391 streaming raw SHA-256 values;
- zero symlinks, special files, unreadable files, and unstable files;
- source-tree writes, modifications, and deletions: 0.

The historical estimate of 11,185 documents and 370,039 provisions described
a broader earlier corpus. The recovered library contains 6,124 consolidated
Gesetze im Internet logical-law candidates and 106,653 provisions explicitly
declared by its records, plus derived metadata, QA material, and archives. No
filesystem timestamp, filename, German language, or duplicate occurrence was
used to invent jurisdiction, legal validity, or an effective date.

## Duplicate and classification evidence

- Exact duplicate groups: 28; members: 332; informational duplicate bytes:
  2,395,295.
- Step 11 normalized duplicate groups: 1.
- Deterministic near-duplicate review candidates: 24.
- License assessment: 18,417 `DECLARED`; 974 `UNKNOWN`.
- Privacy assessment: 18,398 `PUBLIC`; 22 `POTENTIALLY_SENSITIVE`; 971
  `UNKNOWN`.
- Logical quarantine: 18,416 `CLEAR`; 974 `REVIEW_REQUIRED`; 1
  `QUARANTINED`.

Duplicate groups remain evidence only. The implementation performs no delete,
move, rename, hardlink, overwrite, or in-place normalization and does not
select a legal-authority winner.

## External bundle

The complete derived bundle remains outside Git at logical reference
`corpora/manifests/step14/step14-d4acb41c668fe1859cec9f6d1709474f`.
Its manifest digest is
`ab898ea4c3dbfcae12f9c5fcf136914ab68ad11b77ae9431ef648af5c0873f89`;
its summary digest is
`4221da7ca9272fb73eefa1d1f1f1b46d6ab0bda02d2f73272f6e2dd43b86a603`.
The source-tree identity before and after the scan was exactly
`b9732b7db7d74a08fb592e3efb73d7464732d11567e5240f6da3e3fce67eaa70`.
Two safe preliminary attempts exposed Step 14-owned defects, preserved no
false success, and were repaired before the successful deterministic run.

## Source-registry validation

The controlled runtime applied and replayed all nine existing migrations and
required no Step 14 schema migration. It registered 6,124 registration candidates in
Step 9 `REGISTERED` state through Step 6 durable operations. Exact replay
created no duplicate, a conflicting replay was rejected with SQLSTATE `23505`,
a cross-tenant probe was rejected with SQLSTATE `23503`, and the synthetic
review and quarantine controls reached only their explicit non-published
states. Published sources: 0. Runtime DELETE grants: 0.

An initial controlled-validation defect passed an oversized SQL value through
one process argument. Linux rejected 161–396 KiB arguments even though total
`ARG_MAX` was larger. The repaired validator caps each argument at 96 KiB and
24 records; all real first-application batches were at most 93,969 bytes and
all replay batches at most 87,854 bytes. The successful validation evidence is
`docs/evidence/corpus/step14-german-law-corpus-inventory-summary.json`, with
digest `7fde746d8b87abc9a3b9bea9633803a8965cf5abca94175a79054ae9fb65f664`.

## Runtime and authority closure

CockroachDB `v26.2.4` ran as one loopback-only, in-memory, disposable node.
Migration apply/replay, RLS/FORCE RLS, registry isolation, exact replay, and
conflict probes passed. Graceful drain: yes; exact PID exited: yes; ports
closed: yes; temporary store removed: yes; force kill: no; persistent database:
no.

The raw corpus was not stored in CockroachDB or Git. AWS writes, S3 writes,
network acquisitions, model calls, OCR operations, embeddings, and automatic
publication were all zero. Step 9 remains the publication boundary. The
German Law HAT remains non-authoritative and Kernel Core remains domain
neutral. Step 15 temporal and jurisdictional normalization was not started.
