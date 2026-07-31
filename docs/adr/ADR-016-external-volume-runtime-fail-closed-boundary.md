# ADR-016: Verify External Derived Storage at Every Runtime Operation

- Status: Accepted
- Date: 2026-07-30

Implementation and approved live validation are complete in the intended
Step 8 closure commit. See the
[`closure record`](../audits/STEP_8_EXTERNAL_VOLUME_RUNTIME_INTEGRATION_CLOSURE_1A.md)
and
[`sanitized live evidence`](../evidence/external-volume/step8-external-volume-validation.json).

## Context

Step 0B prepared a machine-local external volume and migration toolkit. It did
not give later application code a typed runtime boundary. A disconnected,
remounted, read-only, substituted, full, or symlinked volume could otherwise
cause a large write to land on the internal filesystem or an unsafe path.

Step 7 established exact immutable S3 snapshot evidence. An external-volume
copy remains derived staging and must not inherit S3 or semantic authority.

## Decision

Implement a standard-library Step 8 runtime adapter with:

- explicit private machine configuration parsed without shell execution;
- fresh Linux mount and block-device inspection for every operation;
- exact marker, repository, filesystem, transport, path, and space checks;
- distinct system-root and external filesystem device identities;
- a fixed operation-to-directory and failure-policy map;
- zero internal-disk fallback paths;
- canonical relative-path containment and symlink/special-file rejection;
- bounded exact reads;
- atomic, durable, no-overwrite exact-byte writes;
- narrow detection and preservation of interrupted staging artifacts;
- sanitized storage-only status and write evidence.

Optional caches may be disabled without replacement. Required operations fail
closed. The adapter exposes no delete or overwrite operation for durable
external data.

Keep the concrete Linux subprocess probe outside the Step 7 storage package.
The storage package remains import-inert and injectable, preserving its
no-shell dependency boundary.

## Consequences

- A mounted directory on the root filesystem cannot impersonate the external
  data root.
- Reconnect or automount path changes require an explicit local configuration
  update and a successful preflight.
- Callers must handle typed fail-closed or disabled-operation results.
- No large cache silently moves to the system disk.
- A power-loss staging artifact blocks a duplicate write until it is audited.
- External bytes remain `EXTERNAL_DERIVED` and
  `STORAGE_EVIDENCE_ONLY`.
- Step 10 ingestion states, source publication, credentials, CockroachDB node
  storage, HAT runtime, UI, and AOIA-Core remain out of scope.
