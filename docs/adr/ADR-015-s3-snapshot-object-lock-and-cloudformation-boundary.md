# ADR-015: S3 Snapshot Object Lock and CloudFormation Boundary

- Status: Accepted for Step 7 implementation; deployment is human-gated
- Date: 2026-07-30

## Context

Step 7 was deferred because S3 returned `NotSignedUp`. Step 9 was then
completed without an S3 dependency. The user later reopened Step 7 on top of
the completed Step 9 baseline after non-root SSO identity and read-only S3
availability were verified.

The repository contained no infrastructure framework or dependency manifest.
CDK v2 would add Node or Python dependency management, generated synthesis
state, and a possible account bootstrap requirement for one bounded bucket.
The earlier approved draft also selected CloudFormation.

## Decision

Implement a narrow dependency-injected S3 snapshot adapter for
`S3_GLOBAL_LOCKED_SNAPSHOT`. Use existing canonical serialization and SHA-256
helpers. Require deterministic object keys, exact S3 version IDs, checksums,
Object Lock `GOVERNANCE`, explicit retention, byte read-back verification, and
storage-only audit evidence.

Use a single native CloudFormation JSON template for IaC readiness. It defines
one Object-Lock-enabled bucket and its deny-only protective bucket policy. It
does not define IAM identities, application runtime services, Step 8
infrastructure, Step 9 resources, or Step 10 orchestration.

Require a reviewed change set and explicit human approval before the first
CloudFormation write. Retain the bucket and policy on stack deletion or
replacement. Do not grant deletion or `s3:BypassGovernanceRetention`.

## Authority boundary

S3 persists and verifies bytes. S3, CloudFormation, an AWS SDK, a model, a
HAT, or a critic cannot grant semantic authority, source eligibility,
publication, approval, commit, execution, state transition, or Control Write
authority.

A Step 9 artifact digest may be bound into a snapshot manifest. That reference
does not change the source registry, provenance DAG, or publication state.
Step 9 migrations and evidence remain historically unchanged.

## Consequences

- Importing storage code performs no network operation.
- The host application injects an SDK client and typed non-secret
  configuration.
- Private user snapshots remain a separate storage class and cannot be sent
  through the locked global adapter.
- CloudFormation requires no CDK bootstrap and introduces no package
  dependency.
- Object Lock cannot be disabled casually. Retained object versions remain
  billable and undeletable until retention expires.
- A failed stack or replacement may leave retained resources requiring
  explicit operator reconciliation.
- Step 8 remains unopened. Its later audit may consume Step 7 configuration
  and evidence contracts but cannot reinterpret them as external-volume
  authority.

## Rejected alternatives

- CDK v2 was rejected for Step 7 because it would add a new dependency and
  bootstrap surface disproportionate to one bucket.
- Direct imperative bucket configuration was rejected as the production
  design because it is less reproducible and auditable.
- Terraform, Pulumi, and SAM were rejected because the repository has no such
  standard and they would create competing infrastructure frameworks.
- Automatic bucket cleanup and destroy semantics were rejected because they
  are misleading and unsafe for Object Lock.
- A shared locked bucket for private user content was rejected under ADR-006.
