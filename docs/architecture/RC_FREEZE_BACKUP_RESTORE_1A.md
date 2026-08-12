# RC Freeze, Backup and Restore 1A

## Status and boundary

Step 42 freezes the post-Step41 runtime into a deterministic release-candidate
identity and proves a native CockroachDB backup by restoring it into a separate
disposable cluster. This is a controlled single-node recovery proof, not a
production HA/DR claim. It adds no route, evidence, Personal Memory, reviewer,
provider, approval, commit, activation, publication, or execution authority.

Step 43 remains outside this architecture. It may polish documentation and demo
materials, but it may not silently change frozen runtime semantics.

## Release-candidate identity

The machine-readable RC manifest is
`../evidence/release/step42-rc-manifest-1a.json`. It binds:

- the pushed Step41 parent SHA, canonical branch, and remote;
- the ordered migration manifest and every migration digest;
- the Step40 runtime profile;
- the approved OpenRouter `moonshotai/kimi-k2` configuration;
- Draft V1, Draft V2/verifier, and Critic contract digests;
- the pinned `intfloat/multilingual-e5-small` configuration;
- source-registry, German Law HAT/corpus/publication manifests;
- Personal Memory schema/state-machine migrations;
- audit schema/hash-chain migration;
- Python/UI dependency manifests, CockroachDB pin, and UI static assets;
- the recovery-asset manifest; and
- a sorted digest of runtime source, scripts, configuration, schemas, SQL, and
  dependency manifests.

The manifest deliberately binds the Step41 parent and a deterministic runtime
content digest. The final pushed Step42 commit is reported separately as the RC
Git identity, avoiding an impossible self-referential commit hash.

## State classification

The full deterministic inventory is
`../evidence/release/step42-recovery-asset-manifest-1a.json`.

| Class | Step42 treatment |
|---|---|
| `AUTHORITATIVE_BACKUP_REQUIRED` | One RC-aligned CockroachDB database is backed up natively and restored. It includes source/provenance, retrieval, Personal Memory, audit/review, Critic-candidate, idempotency, and recovery state persisted in the database. |
| `AUTHORITATIVE_EXTERNALLY_PROTECTED` | The pushed Git RC, frozen source/corpus manifests, and exact immutable S3 object versions remain in their existing protected systems. Step42 neither copies nor mutates AWS objects. |
| `REBUILDABLE_FROM_CANONICAL_INPUTS` | Pinned model artifacts, embeddings, and external-volume caches are rebuilt or re-staged only from frozen model/corpus identities. They never become authority. |
| `EPHEMERAL_DO_NOT_BACKUP` | Process IDs, sockets, local stores, temporary logs, and validation directories are removed. |
| `SECRET_DO_NOT_ARCHIVE` | Provider, DB, OIDC/session/signing, Commit Helper, reviewer, and cloud secret values are excluded. Only logical purpose names remain in existing configuration and capability contracts. |

## Backup consistency and integrity

The validation constructs a complete RC-compatible disposable database, runs
all 18 migrations and their replay, and populates:

- the canonical German Law source/retrieval/embedding fixture;
- active, committed, and awaiting-approval Personal Memory fixtures;
- owner and cross-model active-patch retrieval proofs; and
- representative append-only audit chains and heads.

It then freezes a watermark from migration IDs, bounded critical-table counts,
and deterministic hash projections of source registry/provenance/publication,
German Law chunks, embeddings, Personal Memory slot/quota/model bindings,
proposal/approval/commit/transition/patch state, idempotency operations, audit
events/heads, and migration state. Native
`BACKUP DATABASE ... INTO nodelocal://... WITH detached` creates
the artifact. `SHOW BACKUP ... WITH check_files` validates native files, and a
second sorted SHA-256 tree receipt binds every regular backup file, size, the
RC manifest, and the whole tree.

The nodelocal directory exists only inside a mode-0700 Step42 directory under
`/tmp`. This is controlled validation protection, not a production encryption
or KMS claim. The artifact is never committed and is removed after validation.

## Isolated restore and destructive guards

Restore accepts only:

- an existing non-symlink mode-private directory directly under `/tmp` whose
  name starts `mp-step42-recovery-`;
- source databases starting `mp_step42_source_`;
- restore databases starting `mp_step42_restore_`;
- different source and target names; and
- a target marker directly below the owned root with an exact `restore-`
  prefix.

The guard rejects `/`, `/tmp`, the home directory, repository root, shared
external-volume roots, unowned database names, symlinks, open directory modes,
and source/target identity reuse before mutation.

The source runtime is stopped before a new restore runtime starts. The restore
cluster first applies the frozen migrations to a disposable bootstrap database
so cluster roles exist, removes that bootstrap database, restores under a new
database name with grants, and replays migrations. The restored database must
skip all 18 migrations and reproduce the exact pre-backup watermark.

## Post-restore acceptance

Recovery is not considered successful at database startup. The controlled
validation also requires:

- Step36 security-catalog equality and no broad BYPASSRLS widening;
- exact/full-text/vector/hybrid German Law retrieval with hard filters;
- Step21 temporal resolution;
- one bounded real approved-provider Draft V1 through Verified Answer lineage;
- restored active Personal Memory retrieval, non-active exclusion, owner and
  tenant isolation, and canonical-conflict suppression;
- restored Step33 chain verification, export-compatible history, and detection
  of every controlled in-memory tamper case without silent repair;
- zero promoted Critic candidate, with Critic optional and non-authoritative;
- Step40 profile digest/readiness with Critic disabled intentionally; and
- complete owned database, role, process, port, and temporary-tree cleanup.

## Authority and failure rules

- A backup is not complete until its exact receipt verifies and restore passes.
- A restored database is not ready until migrations, RLS/roles, source
  provenance, Personal Memory, audit, retrieval, model/verifier, and 4 GB smoke
  checks pass.
- Missing/corrupt/extra backup bytes fail before restore success.
- Interrupted native jobs are bounded, polled by typed numeric job identity,
  and never become success on timeout/failure.
- Restore never falls back to a master/admin credential for application work.
- Provider credentials remain purpose-bound and absent from backup, evidence,
  commands, logs, and child environments other than the approved provider
  boundary.
- Resource recovery never skips RLS, audit, temporal resolution, verification,
  or human owner approval.

## Freeze policy

After the Step42 closure commit is pushed, the RC admits no new feature,
provider/model, dependency, schema, prompt-semantic, authority-policy,
source/corpus, Personal Memory state-machine, or Critic-authority change. A
runtime/security defect found later requires explicitly reopening and
revalidating the RC. Step43 may add only documentation, demo automation, and
submission artifacts consistent with this boundary.

## Limitations

- Controlled validation uses two sequential local single-node CockroachDB
  processes, not managed multi-node production infrastructure.
- It inventories and verifies immutable source-version references but performs
  no AWS read/write or Object Lock mutation.
- The local nodelocal backup is mode-private but is not a production encrypted
  backup design.
- Recovery durations and zero tested-fixture data loss are observations for the
  validation host, not production RTO/RPO guarantees.
- Derived model/embedding caches are reused or rebuilt, not archived.
