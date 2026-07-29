# Memory Patch — Step 9 Source Registry, Provenance, and Publication Closure 1A

## Status

`IMPLEMENTATION AND LIVE VALIDATION COMPLETE`

This record belongs to the intended single Step 9 closure commit. It becomes
completion evidence only when that commit is reachable on `origin/main`. The
operator closure report records the resulting commit identity after push.

## Recovery checkpoint

Step 9 was already implemented, restored, and uncommitted after an operating
system reinstall. The continuation did not regenerate or overwrite that
worktree.

Recovery verification established:

- required Step 6 starting commit
  `cc5c9f5a1e145ffdadfe9ef8347f087f8f663812`;
- branch `main` with the same starting commit on `origin/main`;
- clean index and the expected 6 modified plus 12 untracked Step 9 files;
- exact restored Step 9 checksum agreement: `18/18 PASS`;
- restored initial migration `0006` checksum
  `d6a4ac7de0e6803605324dacde7fce4596f4a0bd6ccbd65dec27574b5c575356`;
- verified read-only recovery evidence kept outside the repository.

The previous removable-media runtime had shown binary instability. It was not
reused. The operating system was reinstalled, the workspace was restored onto
a verified Unix-native filesystem, and a new deterministic storage probe
passed before runtime acquisition.

One master-backup `.git` directory had a restoration-time, directory-only
timestamp anomaly recorded by the restore task. No backup file, logical Git
index, or content changed. The backup was not used as a live repository and
was not inspected with Git, modified, repaired, or normalized during Step 9.
The verified live workspace, restored checksums, and read-only recovery
evidence were the continuation authority.

## Audited corrections

The restored implementation was read before editing. Bounded pre-commit
corrections:

- require exact publication genesis in typed, repository, SQL, and RLS paths;
- bind publication actor type and reference into durable event identity;
- bind eligibility to every reachable provenance branch and reject a graph
  from another source scope;
- make automatic-time publication retries preserve their semantic Step 6
  idempotency request digest;
- exercise Step 6 durable operation state for source, provenance, and
  publication mutations;
- adapt validation-only runtime, report, and bytecode-cache paths to the
  restored workspace with strict containment and no fallback;
- add database-read chain, tamper, actor, compare-and-set, rollback, role,
  panic, and exact cleanup probes.

Migration `0006` had never been committed or applied to an authoritative
persistent environment. It was corrected in place rather than creating
`0007`. Its final SHA-256 is
`921f5e1bb16142c082b1e91fbbaae729af3aad6f62fd0a5a0a15cda5f3fa5347`,
and the manifest records the same value. Migrations `0001` through `0005`
remain unchanged.

## Fresh CockroachDB runtime

A fresh official archive for CockroachDB `v26.2.4` was acquired directly from
the pinned vendor endpoint after the workspace integrity probe.

- archive SHA-256:
  `3c7de055c07f9101eb0f71b3f5e6b489b0fcf449d3d5a55bfe61eff4f935ce8f`;
- archive digest: stable across 10 reads;
- gzip integrity and member traversal checks: PASS;
- binary SHA-256:
  `a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf`;
- binary digest: stable across 10 reads and five paired-copy checks;
- three version executions: `v26.2.4`, `x86_64-pc-linux-gnu`, PASS;
- binary identity before startup and after shutdown: identical.

No historical CockroachDB binary or store was restored or reused.

## Offline and regression validation

Before live validation:

- contract validator: PASS;
- capability, migration, RLS, persistence, and source-registry offline
  validators: PASS;
- schema migration focused suite: 57 PASS;
- RLS focused suite: 58 PASS;
- persistence focused suite: 46 PASS;
- source-registry focused suite: 74 PASS;
- compileall using the bounded data-disk cache: PASS;
- diff whitespace validation: PASS.

After the successful live run, the final full repository regression passed
`516/516`, followed by every required offline validator and diff check again.

## Live migration and security results

The bounded harness started one disposable CockroachDB node on loopback with
an in-memory store and external I/O disabled.

- migrations `0001` through `0006` applied from zero: 6;
- checksum replay applied: 0 and skipped: 6;
- second fresh database applied: 6;
- schema/security reproduction digest:
  `60796a86a85fe0ad8b3e6bf612c2a45ab9fb379596a091fd58abbb6772cde0b7`;
- Step 9 security digest:
  `5943da10368467f0fc82e76bcfceec2f767dfdab6a2bc77442dea476f8137a44`;
- total live probes: `48 PASS / 0 FAIL`.

Every new table had RLS and FORCE RLS. Runtime and test roles had no
`BYPASSRLS` or administrative capability. Cross-tenant reads/writes and
same-tenant cross-user private reads/writes were denied. Tenant-only and unset
contexts exposed no private data. Runtime DELETE was denied.

## Provenance and publication results

Live validation proved:

- shared and private typed source registration;
- exact registration genesis enforcement;
- exact provenance replay and conflict handling;
- self-edge and cycle rejection;
- deterministic whole-DAG eligibility;
- eligible and prohibited policy outcomes;
- the legal `REGISTERED -> REVIEW_REQUIRED -> ELIGIBLE -> PUBLISHED` chain;
- direct `REGISTERED -> PUBLISHED` rejection;
- stored event sequence, actor identity, digest links, and terminal pointer;
- stale compare-and-set changed zero rows;
- event UPDATE and DELETE denial;
- bad previous-event link rolled back the event, Step 6 operation, and pointer
  together;
- `MODEL`, `HAT`, and `CRITIC` publication actors were rejected;
- publication granted no answer, approval, commit, or execution authority.

The tracked sanitized evidence is
[`step9-source-registry-validation.json`](../evidence/cockroachdb-v26-2/step9-source-registry-validation.json).

## Cleanup and scope

Both disposable databases, all disposable and fixed roles, owned process,
loopback listeners, and the temporary store were removed. Shutdown was
graceful, no force-kill was used, and the owned server log contained no panic
marker. The disposable live store was already removed. The fresh download,
binary, and bytecode cache remain untracked, and their exact task-owned roots
are removed only after the successful push.

No AWS call was made. No Step 7 implementation, Step 8 production adapter,
Step 10 ingestion behavior, NVIDIA/NOOA/OpenShell component, model-provider
dependency, real source content, or German-law rule was added.

The disposable insecure single-node run proves the bounded SQL and application
contracts. It does not prove production certificates, end-user
authentication, a distributed deployment, S3 behavior, or real-source
ingestion.

## Roadmap status

```text
Step 7: DEFERRED — NOT COMPLETE
Step 8: DEFERRED — NOT COMPLETE
Step 9: COMPLETE AND PUSHED
Step 10: NOT STARTED
```

`Step 9: COMPLETE AND PUSHED` is true for repository audit only after this
intended closure commit reaches `origin/main`.

Nominal canonical next step:
`Step 10 — Idempotent S3–CockroachDB Ingestion Saga 1A`.

Step 10 remains operationally dependent on deferred Step 7 and was not
started.
