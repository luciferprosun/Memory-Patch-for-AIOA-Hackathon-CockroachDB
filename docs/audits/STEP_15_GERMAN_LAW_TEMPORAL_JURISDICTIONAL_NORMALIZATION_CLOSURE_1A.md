# Step 15 — German Law Temporal and Jurisdictional Normalization 1A

## Verdict

`COMPLETE AND PUSHED at actual closure commit` once this record is reachable on
`origin/main`. Step 16: NOT STARTED.

## Input boundary and real-corpus run

Step 15 accepted the fixed, verified Step-14 manifest
`ab898ea4c3dbfcae12f9c5fcf136914ab68ad11b77ae9431ef648af5c0873f89` and
the unchanged portable source-root identity
`5c149c3e2f3fd7a90f5c416d659dc0e36f27873d7eac9dba24f0c81830747d06`.
The pre- and post-run source-tree digest was exactly
`b9732b7db7d74a08fb592e3efb73d7464732d11567e5240f6da3e3fce67eaa70`.

The corrected deterministic run is the external, root-relative bundle
`corpora/manifests/step15/step15-a2bf509e317ee6fa2ad5834396630e43`. Its
manifest digest is
`7094358f7c9bb6acf62160484a017074da70361c73e4e5bbd7623f700414b125`; its
logical output digest is
`335792d021bfdb3f822a3edaef9e8c8430adb79fad4ce1d36943e90d39f5099e`; its
summary digest is
`8efb0fdd331e24d1a4f7d60416b964e0061d1023b165d22b72e9120048a0c1a9`.
All outputs are external derived evidence, not Git corpus content.

The run streamed 19,391 Step-14 inventory records and evaluated 6,134
hash-bound structured metadata records. It created 6,124 document identities,
6,124 version identities, 24 review-only version relationships, and 6,134
review-only Step-9 normalization proposals. It recorded 6,134 `DE_FEDERAL`
jurisdiction results from structured GII metadata. It did not infer a state or
EU scope from language, directory, filename, hostname, retrieval time, or
filesystem metadata.

It preserved 6,133 `EXECUTED_AT`, 6,134 `RETRIEVED_AT`, and 6,134
`SOURCE_BUILD_AT` facts. No missing `published_at`, effective interval,
promulgation, adoption, applicability, verification, or supersession fact was
invented. One invalid temporal value and 29 bounded invalid optional metadata
markers remain typed review evidence; conflicts are not silently resolved.

## Corrected attempts

The first external derived-only run exposed a Step-15-owned defect: 29 valid
records carried an optional descriptive `version_basis` value longer than the
initial marker limit, causing their otherwise valid facts to be omitted. The
rule version was advanced to `german-law-temporal-normalization-1a.1`, valid
facts are now retained with bounded review evidence, and the successful run
uses a new no-overwrite bundle. The earlier bundle was preserved rather than
rewritten or deleted.

The first disposable CockroachDB validation then exposed a second Step-15
defect: `STEP15_REGISTRY_ROW_MISSING`; a replay assertion parsed the CLI TSV
header as a data row. The fix uses the repository TSV parser and requires
exactly one hash-bound row.
The failing disposable runtime executed its graceful cleanup path; it left no
owned process, port, temporary store, database, or evidence file. The rerun
used a fresh runtime and produced the canonical success evidence below.

## Controlled CockroachDB and Step 9 compatibility

The successful validation used loopback-only, in-memory CockroachDB `v26.2.4`,
applied and replayed all nine existing migrations, and added no Step-15
migration. One metadata-only Step-9 control registration proved compatibility;
exact replay passed, a conflicting replay was rejected with SQLSTATE `23505`,
and published sources remained zero. RLS/FORCE RLS passed and runtime DELETE
grants remained zero.

The evidence is
`docs/evidence/corpus/step15-german-law-temporal-jurisdictional-summary.json`
with digest
`de5264ab07c001f27a27722ae7b13bf4bdef3021d8b489191b885ab16be1d5ef`.
Graceful drain: yes; exact PID exited: yes; ports closed: yes; temporary store
removed: yes; force kill: no; persistent database: no.

## Validation

`python3 -m compileall src tests scripts` passed. Focused Step 8/9/11/13/14/15
regressions passed 376 tests. The final repository command
`python3 -m unittest discover -s tests -p 'test*.py' -q` passed 1,127 tests.
The migration manifest and all nine existing migrations were checked offline
and in the controlled first-apply/no-op replay. The Step-15 evidence digest,
external bundle manifest, logical output digest, and source-tree digest were
independently reverified before the database validation.

## Safety and deferrals

Source-tree writes, modifications, and deletions were all zero. AWS writes,
S3 writes, network acquisitions, model calls, OCR, embeddings, publication,
approval transitions, raw corpus bodies in CockroachDB, and Step 16 work were
all zero. The normalizer neither resolves a legal question nor determines law
applicable to a person or time. It does not publish, approve, commit, activate,
or grant HAT authority. Step 9 remains the publication boundary; Step 16 owns
publication and corpus verification; Step 21 owns question-time temporal
resolution.
