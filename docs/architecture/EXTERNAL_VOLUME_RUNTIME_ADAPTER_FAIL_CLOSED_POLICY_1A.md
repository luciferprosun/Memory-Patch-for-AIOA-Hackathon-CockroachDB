# External Volume Runtime Adapter and Fail-Closed Policy 1A

## Status and boundary

Implementation and approved live validation are complete in the intended
Step 8 closure commit. The closure becomes completion evidence only after that
commit is reachable on `origin/main`; see
[`STEP_8_EXTERNAL_VOLUME_RUNTIME_INTEGRATION_CLOSURE_1A.md`](../audits/STEP_8_EXTERNAL_VOLUME_RUNTIME_INTEGRATION_CLOSURE_1A.md).

Step 8 turns the prepared Step 0B volume contract into an application runtime
boundary for `StorageClass.EXTERNAL_DERIVED`. The adapter handles derived and
recoverable data only. It does not store credentials, host an active
CockroachDB node, become the sole copy of authoritative state, approve or
publish data, call AWS, implement the Step 10 saga, or grant semantic
authority.

The committed configuration remains generic. Exact machine mountpoints, UUIDs,
labels, and local paths stay in the ignored private file
`.local/external-data.env`.

## Fresh verification

Every path resolution, exact read, and atomic write performs a fresh
verification. The runtime requires all of the following to agree:

- the configured path is the exact `findmnt` target;
- the mount source is a block device and is distinct from the system root
  filesystem;
- filesystem type, UUID, label, and transport match explicit configuration;
- mount options include `rw`, `nodev`, and `nosuid`, omit `ro`, and the block
  device does not report read-only state;
- the current user has the access needed by the requested operation;
- available space exceeds the larger of 20 GiB or ten percent of capacity;
- the data root is exactly
  `AIOA_DATA/Memory-Patch-for-AIOA` below the mountpoint and is on the same
  filesystem;
- the fixed Step 0B marker is a bounded non-symlink regular file with safe
  mode, exact project, repository, creation-HEAD, device, and filesystem
  binding;
- every prepared directory is a real directory on the verified filesystem.

The public status and write evidence hash the filesystem UUID into a stable
device reference. Raw UUID, label, device source, mountpoint, and local path do
not enter committed evidence.

## Safe configuration

`load_external_volume_environment()` parses only quoted `NAME="value"`
assignments and prior `${NAME}` references. It does not execute the local file,
invoke a shell, allow command substitution, allow forward references, or
accept a symlink or group/world-accessible configuration file.

The runtime requires explicit transport and capacity policy in addition to the
Step 0B identity values. Missing or malformed values fail closed.

## Operation-specific failure policy

No operation can receive an internal-disk fallback path.

| Operation | External root | Unavailable behavior |
| --- | --- | --- |
| `CORPUS_REPLICA` | `corpora/raw` | `FAIL_CLOSED` |
| `EMBEDDING_CACHE` | `embeddings` | `DISABLE_OPERATION_WITHOUT_FALLBACK` |
| `INDEX_CACHE` | `indexes` | `DISABLE_OPERATION_WITHOUT_FALLBACK` |
| `INGESTION_STAGING` | `ingestion/downloads` | `FAIL_CLOSED` |
| `PACKAGE_CACHE` | `cache` | `DISABLE_OPERATION_WITHOUT_FALLBACK` |
| `APPLICATION_SNAPSHOT_STAGING` | `snapshots/application` | `FAIL_CLOSED` |
| `DATABASE_EXPORT` | `snapshots/database-export` | `FAIL_CLOSED` |
| `BACKUP` | `backups` | `FAIL_CLOSED` |
| `VALIDATION_EVIDENCE` | `reports` | `FAIL_CLOSED` |

An optional cache may be disabled, allowing a caller to continue without that
cache. The adapter still returns no alternative path. Required acquisition,
snapshot, export, backup, and validation operations stop.

## Containment and file policy

Operation paths must be canonical relative POSIX paths. Absolute paths,
`..`, dot segments, duplicate separators, backslashes, control characters,
unprepared parents, mount roots, and the marker name are rejected.

Every existing parent is checked with `lstat`, same-filesystem identity, and a
non-following directory open. Reads use `O_NOFOLLOW`, compare pre-open and
post-open inode identity, require regular files, enforce a 64 MiB bound, and
verify exact byte length and SHA-256. Symlinks, FIFOs, sockets, devices, and
other special files are rejected.

The adapter never creates a directory. Prepared layout changes remain a
separate operator action.

## Atomic no-overwrite writes and recovery

`atomic_write_exact()` accepts immutable bytes plus their expected length and
SHA-256. A mismatch fails before any write.

The write protocol is:

1. reverify volume identity, write access, marker, layout, and capacity;
2. reject an existing target of any type;
3. reject and preserve a target-bound incomplete Step 8 staging artifact;
4. create one mode `0600`, `O_EXCL`, `O_NOFOLLOW` staging file in the target
   directory;
5. write, file-`fsync`, and exact-read the staging bytes;
6. atomically hard-link to the absent final name, so an existing target cannot
   be replaced;
7. directory-`fsync`, remove only the newly created staging name, and
   directory-`fsync` again;
8. exact-read the final file and verify length and SHA-256.

A power loss can leave a target-bound name beginning
`.aioa-step8-atomic-`. The next attempt reports it and stops without deleting
it. `incomplete_atomic_artifacts()` provides a narrow read-only inspection for
that exact target.

The public API contains no durable-file delete, overwrite, directory-create,
system-drive fallback, credential, or database-store operation.

## Step 7 compatibility and authority

The live Step 8 probe uses the same fixed 88-byte synthetic fixture and
SHA-256 used by Step 7:

```text
d1bedd6275072d01cc932af7a57d826837e27b9c7d1039bb558d4039abf819fc
```

Equality proves exact local staging compatibility only. The external copy is
derived storage, not an S3 version, Object Lock evidence, canonical source,
publication decision, approval, or Memory Patch commitment.

## Live validation gate

`scripts/run_external_volume_validation.py --preflight` performs no data-tree
write. The fixed live write requires both `--write-validation` and exact
project/device-reference confirmations. It writes only the displayed absent
target, never replaces it, and returns `ALREADY_VALID_NO_WRITE` if that exact
target already exists with the expected bytes.

The live write must not be run until the operator has approved its exact
displayed plan.

The approved fixed write completed once. Its sanitized exact-byte result is
[`step8-external-volume-validation.json`](../evidence/external-volume/step8-external-volume-validation.json).
