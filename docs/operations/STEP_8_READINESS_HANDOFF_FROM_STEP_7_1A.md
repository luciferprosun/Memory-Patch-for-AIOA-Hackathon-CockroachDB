# Step 8 Readiness Handoff from Step 7 1A

## Status

Step 8 remains `DEFERRED BY USER - NOT COMPLETE` and was not started by Step
7. This note prepares only the later audit entry point.

Readiness verdict: `READY FOR STEP 8 AUDIT` when the Step 7 closure commit
containing this record is reachable on `origin/main`. This verdict authorizes
only a separately requested audit, not Step 8 implementation.

## Expected external-volume root

The committed contract expects:

```text
${AIOA_EXTERNAL_MOUNTPOINT}/AIOA_DATA/Memory-Patch-for-AIOA
```

The exact machine mountpoint, filesystem UUID, label, filesystem type, marker,
read/write state, free space, and containment must be read from ignored local
configuration and verified live by Step 8. They are not asserted by this
handoff.

## Step 7 outputs Step 8 may consume

Step 8 may consume only non-secret Step 7 contracts:

- bucket name, Region, key prefix, expected Object Lock mode, retention days,
  and deployment identifier;
- deterministic snapshot ID, canonical SHA-256, byte length, representation
  version, media type, object key, S3 version ID, and retain-until intent;
- `SnapshotStorageEvidence` with hashed bucket reference, exact version,
  checksum, retention, and verification flags;
- sanitized typed storage failures.

Those values do not authorize an external-volume write, S3 write, source
publication, state transition, approval, commit, execution, or fallback.

## Assumptions the Step 8 audit must verify

Step 8 begins by auditing the existing Step 0B external-volume implementation,
not by trusting that a USB device is merely connected. It must verify:

- the exact configured mountpoint and external data root;
- USB transport and expected device identity;
- filesystem UUID, label, type, and mount source;
- read/write mount options and conservative free space;
- the project marker and its repository identity binding;
- containment of every allowed path;
- symlink and special-file policy;
- operation-specific failure behavior;
- no silent fallback to the system drive;
- compatibility between local staging evidence and Step 7 canonical hashes;
- that no credential or authoritative database state is placed on the
  external volume.

## Boundary

Step 7 did not inspect broad external-drive contents, run the Step 8 discovery
or preflight scripts, modify ignored local volume configuration, create a
directory, symlink, mount, service, permission, or migration, or change the
prepared USB infrastructure.

The readiness verdict means only that the Step 8 audit may begin under a
separate prompt after Step 7 is complete and pushed. It does not mean the
external volume has passed that audit.
