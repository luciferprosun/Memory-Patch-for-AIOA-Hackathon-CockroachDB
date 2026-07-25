# ADR-006: Separate Private and Global Source Snapshots

- Status: Accepted
- Date: 2026-07-25

## Context

Public registered sources may need immutable versioned snapshots. Private user
documents require owner access, bounded retention, export, and deletion.

## Decision

Represent global locked and user-private S3 snapshots as different storage
classes. Never apply a permanent global retention assumption to private user
content. Do not export private payloads through a common unfiltered CDC path.

## Consequences

Future infrastructure requires separate access and retention policies. Audit
metadata references protected payloads without embedding them. This step
creates no bucket or cloud resource.
