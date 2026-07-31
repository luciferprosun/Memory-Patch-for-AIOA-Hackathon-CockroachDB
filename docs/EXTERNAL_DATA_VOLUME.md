# External Data Volume Contract

The Memory Patch repository keeps source, tests, and committed configuration
on the system drive. Large corpora, embeddings, generated indexes, ingestion
downloads, package caches, snapshots, migration evidence, and backups belong
on a separately verified external volume. This prevents replaceable data from
exhausting the smaller system filesystem.

This storage layer does not implement the Knowledge Kernel or CockroachDB
integration. It creates a fail-closed contract that later components can use.

## Identification and safety boundary

Never identify a volume using only `/dev/sdX`, a partition number, or its
size. Device names can change after reconnecting hardware. Selection requires
agreement between:

- USB transport;
- expected model and approximate capacity;
- filesystem label;
- filesystem UUID;
- current mountpoint and filesystem type;
- an explicit read/write mount state;
- exact free-space reporting;
- successful write, read-back, rename, and symlink probes.

Run the read-only discovery script:

```bash
scripts/external_data/discover.sh
```

It displays candidates but never mounts, unmounts, selects, or writes to a
device. Disk formatting, partitioning, repair, and `/etc/fstab` management are
deliberately outside this toolkit.

## Local configuration

Copy the committed example and edit the ignored copy:

```bash
mkdir -p .local
cp config/external-data.env.example .local/external-data.env
chmod 600 .local/external-data.env
```

Set the verified values using placeholders such as:

```bash
AIOA_EXTERNAL_MOUNTPOINT="/absolute/mountpoint"
AIOA_EXTERNAL_DATA_ROOT="${AIOA_EXTERNAL_MOUNTPOINT}/AIOA_DATA/Memory-Patch-for-AIOA"
AIOA_EXTERNAL_DEVICE_UUID="verified-filesystem-uuid"
AIOA_EXTERNAL_DEVICE_LABEL="verified-filesystem-label"
AIOA_EXTERNAL_FILESYSTEM_TYPE="ext4"
AIOA_EXTERNAL_DEVICE_TRANSPORT="verified-transport"
AIOA_EXTERNAL_MINIMUM_FREE_BYTES="21474836480"
AIOA_EXTERNAL_RESERVE_PERCENT="10"
AIOA_EXTERNAL_MAXIMUM_ATOMIC_WRITE_BYTES="67108864"
```

The local file may contain a machine-specific absolute path and UUID because
Git ignores it. Never copy those values into committed documentation, scripts,
or configuration.

## Prepared layout

Preparation owns only
`${AIOA_EXTERNAL_MOUNTPOINT}/AIOA_DATA/Memory-Patch-for-AIOA`:

```text
Memory-Patch-for-AIOA/
├── .aioa-external-volume.json
├── corpora/
│   ├── incoming/
│   ├── raw/
│   ├── normalized/
│   ├── rejected/
│   └── manifests/
├── embeddings/
│   ├── active/
│   ├── staging/
│   └── manifests/
├── indexes/
│   ├── active/
│   ├── staging/
│   └── manifests/
├── ingestion/
│   ├── downloads/
│   ├── wheels/
│   ├── source-archives/
│   └── build-cache/
├── cache/
│   ├── huggingface/
│   ├── datasets/
│   ├── transformers/
│   ├── pip/
│   ├── xdg/
│   └── temporary/
├── snapshots/
│   ├── application/
│   ├── database-export/
│   └── manifests/
├── backups/
│   ├── repository-data/
│   ├── migration-rollback/
│   └── manifests/
├── migration/
│   ├── journals/
│   ├── inventories/
│   ├── verification/
│   └── quarantine/
├── logs/
└── reports/
```

The marker is local external-volume state. It binds the project ID to the
verified filesystem UUID, label, type, repository remote, and creation HEAD.
A conflicting marker is never overwritten.

## Preflight and preparation

Preflight validates repository identity, local configuration, mount ownership
and access, `rw` options, UUID, label, filesystem, containment, commands, and
the conservative free-space reserve:

```bash
scripts/external_data/preflight.sh
```

On a new empty volume, the missing marker is reported as an allowed
initialization state. Preview preparation:

```bash
scripts/external_data/prepare.sh --dry-run
```

Apply only with both explicit confirmations:

```bash
scripts/external_data/prepare.sh \
  --apply \
  --confirm-project memory-patch-for-aioa \
  --confirm-uuid "verified-filesystem-uuid"
```

Then require and verify the marker and complete layout:

```bash
scripts/external_data/preflight.sh --require-marker
scripts/external_data/verify.sh
```

Preparation is idempotent. It reports existing directories, creates only
missing approved directories, rejects symlinks or conflicting files, and
validates the result.

## Candidate inventory

The Python inventory component uses only the standard library. It walks
without following symlinks and records deterministic relative paths, types,
regular-file sizes and SHA-256 hashes, symlink targets, apparent bytes, and
allocated bytes.

Inventory a directory only within an explicitly allowed root:

```bash
python3 scripts/external_data/inventory.py create \
  path/to/candidate \
  --allowed-root "$PWD" \
  --output .local/candidate.inventory.json
```

Compare independently generated inventories:

```bash
python3 scripts/external_data/inventory.py compare \
  .local/source.inventory.json \
  .local/destination.inventory.json
```

Missing paths, changing files, special files, and paths outside the allowed
root fail closed. Escaping symlinks are recorded but never traversed.

## Migration protocol

Migration is always a dry run unless `--apply` is supplied. The default scan
checks only the documented generated-data candidates:

```bash
scripts/external_data/migrate.sh --dry-run
```

For each present candidate it checks:

- a regular directory contained by the repository;
- zero Git-tracked paths;
- an existing ignore rule for the eventual machine-local symlink;
- deterministic counts, sizes, hashes, symlinks, and special-file types;
- no escaping symlink;
- no open files when the active-use check is available;
- external capacity for three allocated copies plus the larger of ten percent
  of volume capacity or 20 GiB.

An apply run additionally requires one explicit approved candidate:

```bash
scripts/external_data/migrate.sh \
  --apply \
  --candidate "repository-relative/generated-data" \
  --confirm-project memory-patch-for-aioa \
  --confirm-uuid "verified-filesystem-uuid"
```

The copy-first phases are:

1. Create a private ignored journal and source inventory.
2. Copy without dereferencing symlinks into external quarantine.
3. Independently inventory and compare the quarantine copy.
4. Atomically rename the verified copy to its final location on the same
   external filesystem and verify it again.
5. Rename the untracked source to a unique local hold, create the
   application-facing symlink, and verify reads through that path.
6. Create and verify an independent external rollback copy.

The implementation conservatively retains the local hold after a successful
migration. This preserves an additional original copy; it is never deleted
implicitly. No migration is a valid and expected result when candidates are
absent, tracked, active, ambiguous, unsafe, or too large.

## Rollback

Rollback accepts only an existing valid ignored migration journal and is a
dry run by default:

```bash
scripts/external_data/rollback.sh \
  --dry-run \
  --migration-id "UTC-timestamp-random-suffix"
```

Apply requires the migration ID, project confirmation, and current UUID:

```bash
scripts/external_data/rollback.sh \
  --apply \
  --migration-id "UTC-timestamp-random-suffix" \
  --confirm-project memory-patch-for-aioa \
  --confirm-uuid "verified-filesystem-uuid"
```

Rollback verifies repository and marker identity, UUID, the exact
application-facing symlink, rollback inventory, and a temporary restored copy.
It then replaces the symlink with the verified directory. The external final
and rollback copies remain intact.

## Filesystem compatibility

A verified read/write Linux-native filesystem such as ext4 is suitable for
the prepared corpora, embeddings, generated indexes, downloads, caches,
snapshots, backups, journals, and inventories.

Portable filesystems such as exFAT, VFAT, or NTFS-compatible mounts may be
usable for ordinary data but are not automatically trusted for Unix
permissions, symlinks, sockets, or runtime state. The configured filesystem
must match the live filesystem.

This step never places these items on the external volume:

- an active Python virtual environment;
- an active CockroachDB node store;
- Unix sockets or database runtime files;
- repository source code;
- secrets or credentials.

## Disconnection and startup policy

If the USB volume is disconnected:

1. Stop components that use external data.
2. Reconnect and mount the intended filesystem manually.
3. Run `scripts/external_data/preflight.sh --require-marker`.
4. Run `scripts/external_data/verify.sh`.
5. Resume only after the UUID, marker, mount, and layout pass.

When external storage configuration is enabled but the mount is absent,
read-only, has the wrong UUID, or has a conflicting marker, future application
components must raise a clear storage-unavailable error. They must not create
replacement corpora, embeddings, indexes, snapshots, downloads, or large
caches on the internal system drive.

## Step 8 runtime boundary

Step 8 supplies that application boundary in
`aioa_memory_kernel.storage.ExternalVolumeRuntimeAdapter`. It reparses the
private configuration without executing shell syntax, uses the concrete Linux
probe only at runtime, and freshly verifies mount, block device, UUID, label,
transport, filesystem, options, root-device separation, marker, tree, access,
and space before every operation.

Paths are operation-bound, relative, and non-following. Optional cache
operations may be disabled, but they receive no internal fallback path.
Required operations fail closed. Exact writes use a bounded no-overwrite
atomic protocol and preserve any interrupted target-bound staging artifact for
audit.

Run the no-write preflight and follow the mandatory live-write gate in
[`STEP_8_EXTERNAL_VOLUME_LIVE_VALIDATION_1A.md`](operations/STEP_8_EXTERNAL_VOLUME_LIVE_VALIDATION_1A.md).
The full design is
[`EXTERNAL_VOLUME_RUNTIME_ADAPTER_FAIL_CLOSED_POLICY_1A.md`](architecture/EXTERNAL_VOLUME_RUNTIME_ADAPTER_FAIL_CLOSED_POLICY_1A.md).
The completed sanitized result is recorded in
[`step8-external-volume-validation.json`](evidence/external-volume/step8-external-volume-validation.json)
and
[`STEP_8_EXTERNAL_VOLUME_RUNTIME_INTEGRATION_CLOSURE_1A.md`](audits/STEP_8_EXTERNAL_VOLUME_RUNTIME_INTEGRATION_CLOSURE_1A.md).

## Git boundary

Committed:

- the generic environment example;
- scripts and standard-library inventory implementation;
- tests;
- this documentation;
- narrow ignore rules.

Never committed:

- `.local/external-data.env`;
- actual mountpoints, UUIDs, serial numbers, or usernames;
- external marker or volume contents;
- migration journals and inventories containing local filenames;
- generated caches or migrated data;
- machine-specific application-facing symlinks.
