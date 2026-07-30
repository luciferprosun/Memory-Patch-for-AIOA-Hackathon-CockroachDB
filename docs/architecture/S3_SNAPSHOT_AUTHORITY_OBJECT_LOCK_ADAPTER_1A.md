# S3 Snapshot Authority and Object Lock Adapter 1A

## Scope and historical order

Step 7 was explicitly deferred, Step 9 was completed, and the user later
reopened Step 7 on top of the intact Step 9 commit. This implementation does
not rewrite that history. It adds a storage boundary compatible with the
existing source registry, whole-DAG provenance, and publication state
contracts.

The Step 7 adapter persists and verifies canonical bytes. It does not decide
whether a snapshot is semantically valid, authoritative, eligible, published,
approved, committed, active, or executable. CockroachDB and the deterministic
local contracts retain their documented responsibilities. S3 is storage and
verification infrastructure only.

## Typed runtime configuration

`S3SnapshotConfig` requires:

- an explicit AWS Region;
- one global locked snapshot bucket name;
- an optional deterministic relative key prefix;
- `GOVERNANCE` retention;
- a bounded retention period in days;
- Object Lock as a mandatory capability;
- optional expected bucket ownership;
- explicit SSE-S3 `AES256`;
- `S3_GLOBAL_LOCKED_SNAPSHOT`.

Configuration fails closed on malformed Regions, bucket names, prefixes,
ownership values, retention values, encryption, or storage-class mixing. No
credential, profile, account identifier, bucket ARN, role ARN, or
machine-specific path is hardcoded.

The locked adapter rejects `S3_USER_PRIVATE_SNAPSHOT`. ADR-006 still requires
private user data to use separately protected storage with owner export,
deletion, and bounded-retention semantics. Step 7 does not route private
payloads into the global Object Lock bucket.

## Canonical snapshot identity

`SnapshotEnvelope` uses the existing canonical JSON and SHA-256 helpers. It
supports repository-canonical JSON (`canonical-json-1a`) and explicitly
identified immutable source bytes (`exact-bytes-1a`). The caller supplies the
representation version, media type, exact scope coordinates, creation time,
retention intent, provenance metadata, and optional Step 9 artifact digest.
For exact bytes, a supplied Step 9 artifact digest must equal the payload
SHA-256. The envelope derives:

- canonical UTF-8 payload bytes;
- canonical SHA-256;
- byte length;
- scope digest;
- manifest digest;
- snapshot ID `s3snap-<manifest-sha256>`;
- Base64-encoded SHA-256 for the S3 checksum header.

The manifest digest binds tenant, source, HAT scope, storage class, payload
hash, length, schema and serialization versions, capture time, retention
mode, retain-until time, authority metadata, provenance metadata, and the
optional Step 9 artifact digest. Retain-until values are timezone-aware UTC
whole seconds. An S3 ETag is never treated as a cryptographic digest.

Object keys are deterministic:

```text
<prefix>/global/v1/<scope-digest-prefix>/<scope-digest>/<snapshot-id>.<json|bin>
```

Raw tenant, source, and HAT identifiers are not embedded in object keys.
Random object keys are not an authority identity.

## Explicit network operations

The application injects a boto3-compatible `S3ClientProtocol`. Importing the
package does not load an SDK, resolve credentials, create a client, open a
socket, or contact AWS. Every network call is an explicit adapter method and
is rejected while a Step 6 persistence transaction is open.

The repository has no Python dependency manifest and does not currently
install `boto3` or `botocore`. Step 7 therefore does not add an implicit SDK
or credential bootstrap. A host that already owns AWS dependency management
must inject a compatible S3 client. Offline adapter tests use strict fakes;
the human-gated infrastructure smoke test uses the existing AWS CLI
equivalent and does not install a package.

Before a write, `persist_snapshot` checks:

1. bucket Region;
2. versioning status;
3. Object Lock status;
4. exact default retention mode and days;
5. per-object retention lasting at least the configured period from the
   actual write time.

`PutObject` uses:

- `IfNoneMatch: *`;
- canonical payload bytes and exact content length;
- Base64 SHA-256 checksum;
- deterministic S3 metadata;
- the exact media type;
- `ObjectLockMode: GOVERNANCE`;
- exact `ObjectLockRetainUntilDate`;
- `ServerSideEncryption: AES256`;
- optional `ExpectedBucketOwner`.

A successful response must contain a non-null S3 `VersionId`. The adapter then
performs `HeadObject` for that exact version and validates content length,
metadata, checksum, version ID, encryption, retention mode, and retain-until
time before returning storage evidence.

## Idempotency and read verification

The conditional write prevents an exact key from silently gaining an
ambiguous new version. A `412 PreconditionFailed` is treated as a possible
replay, not as success. The adapter heads the current version, retrieves that
exact version, and verifies bytes plus metadata. Exact equivalence returns
evidence with `idempotent_replay=true`; any mismatch raises a conflict.
Concurrent `409 ConditionalRequestConflict` fails closed without an implicit
unbounded retry.

Retrieval always requires an explicit version ID. It closes the streaming
body and validates:

- exact bytes;
- canonical SHA-256;
- content length;
- expected metadata;
- S3 checksum;
- version ID;
- Object Lock mode;
- retain-until time;
- encryption.

Expired retention does not invalidate later byte verification. It only blocks
a new persist call that would already be outside its promised retention
window.

## Structured storage evidence

`SnapshotStorageEvidence` contains only:

- deterministic snapshot ID and SHA-256;
- content length;
- a hashed bucket reference;
- deterministic object key;
- S3 version ID;
- retention evidence;
- response checksum;
- metadata/content verification flags;
- idempotent replay flag;
- its own evidence digest.

Its fixed authority marker is `STORAGE_EVIDENCE_ONLY`. It has no publication,
eligibility, approval, commit, execution, or source-authority field. Neither
an S3 response nor SDK response can modify a Step 9 registry or publication
record.

## Fail-closed errors

The adapter exposes sanitized typed errors for configuration, capability,
conflict, integrity, malformed responses, access denial, expired sessions,
service unavailability, and other S3 failures. Raw exception messages,
credentials, endpoints, account IDs, bucket names, and object contents are not
copied into errors.

Partial success is not reported as immutable success. A successful upload
followed by missing version, checksum, metadata, encryption, or retention
evidence raises a precise failure and leaves reconciliation to a later
explicit workflow. Step 7 includes no deletion or cleanup API.

## Infrastructure boundary

The repository had no CDK, CloudFormation, Terraform, Pulumi, SAM, or package
dependency baseline. Adding CDK v2 would introduce a second runtime and a
possible bootstrap requirement. The bounded resource is therefore defined in
one native CloudFormation JSON template:

```text
infra/cloudformation/step7-s3-snapshot-authority-1a.json
```

It defines exactly one bucket and one protective bucket policy. The bucket
has Object Lock at creation, versioning, `GOVERNANCE` default retention,
SSE-S3, complete public-access blocking, `BucketOwnerEnforced`, a global
namespace, bounded tags, `DeletionPolicy: Retain`, and
`UpdateReplacePolicy: Retain`. The policy denies non-TLS requests and denies
governance-retention bypass. It grants no permission.

There is no CDK bootstrap, custom resource, IAM role, application service,
lifecycle deletion, automatic object cleanup, or public allow policy.
CloudFormation outputs only the bucket name, Region, key prefix, retention
settings, and stack name. Application code receives those values through
typed configuration and does not import infrastructure code.

The one-bucket Step 7 boundary does not synthesize S3 server-access-log,
CloudTrail, or CloudWatch resources. Direct S3 server access logs are not
CloudWatch Logs, and adding a log-delivery bucket, trail, log group, or service
role would exceed this closure slice. Operational logging remains an explicit
future, separately approved infrastructure decision rather than an
undisclosed side effect.

## Threat model and limitations

Step 7 defends against accidental mutable writes, key ambiguity, metadata
substitution, version substitution, checksum mismatch, retention mismatch,
SDK response malformation, use inside a database transaction, private/global
storage mixing, and storage responses claiming authority.

It does not prove:

- semantic correctness of snapshot content;
- publication eligibility or publication state;
- cross-system ACID between S3 and CockroachDB;
- bucket creation permission;
- future Step 10 ingestion reconciliation;
- private user snapshot lifecycle;
- Step 8 external-volume runtime readiness;
- protection against an independently authorized account administrator
  replacing infrastructure outside this boundary.

Unit tests use injected fakes only. Live resource creation and a synthetic
read-back remain behind the explicit Step 7 AWS write gate.
