# Step 7 S3 CloudFormation Deployment 1A

## Status

This is a reproducible deployment plan, not deployment authorization.
CloudFormation change-set creation, execution, live object writes, commit, and
push must follow the Step 7 human gate and closure policy. Read-only template
validation and collision checks occur before that gate.

## Proposed bounded resource

| Property | Proposed value |
| --- | --- |
| Stack | `memory-patch-step7-s3-1a` |
| Bucket | `aioa-memory-patch-global-3f105fcd-eu-central-1` |
| Region | `eu-central-1` |
| Namespace | global |
| Key prefix | `memory-patch/snapshots/v1` |
| Object Lock at creation | enabled |
| Versioning | enabled |
| Default retention | `GOVERNANCE`, 7 days |
| Per-object retention | explicit `GOVERNANCE`, at least 7 days |
| Encryption | SSE-S3 `AES256` |
| Public access block | all four controls enabled |
| Object ownership | `BucketOwnerEnforced`; ACLs disabled |
| Lifecycle deletion | none |
| Access-log infrastructure | none; outside this one-bucket closure slice |
| Resource removal | retain on delete and replacement |
| Bootstrap | not required |

The bucket name contains no account identifier, email address, full person
name, or credential. Global availability must be checked read-only immediately
before the gated change set. A `404` HeadBucket result supports availability;
`200` or `403` means the proposed global name cannot be treated as available.

## Synthesized resources

The template resolves to exactly:

1. `AWS::S3::Bucket` named `SnapshotBucket`;
2. `AWS::S3::BucketPolicy` named `SnapshotBucketPolicy`.

The policy contains only explicit denies:

- deny all non-TLS S3 requests;
- deny `s3:BypassGovernanceRetention`.

The wildcard S3 action in the TLS statement is a deny condition, not an
administrative permission grant. There is no IAM resource, allow statement,
custom resource, Lambda, CDK bootstrap resource, or unrelated AWS service.
It also creates no CloudTrail trail, CloudWatch Logs resource, or S3
server-access-log destination. Those require additional resources and a
separate approval boundary.

## Non-secret application outputs

CloudFormation outputs:

- bucket name;
- bucket Region;
- object key prefix;
- Object Lock mode;
- retention days;
- stack name as deployment identifier.

The application maps those values into `S3SnapshotConfig`. It does not import
the template and does not receive credentials, account IDs, caller identity,
SSO data, or tokens.

## Least-privilege actions

The deployment identity needs only the CloudFormation actions required to
create, inspect, execute, and read one named change set and stack:

```text
cloudformation:CreateChangeSet
cloudformation:CreateStack
cloudformation:DescribeChangeSet
cloudformation:DescribeStacks
cloudformation:ExecuteChangeSet
cloudformation:TagResource
cloudformation:ValidateTemplate
```

For the template resources it needs these S3 configuration actions scoped to
the proposed bucket:

```text
s3:CreateBucket
s3:GetBucketAcl
s3:GetBucketLocation
s3:GetBucketObjectLockConfiguration
s3:GetBucketOwnershipControls
s3:GetBucketPolicy
s3:GetBucketPolicyStatus
s3:GetBucketPublicAccessBlock
s3:GetBucketTagging
s3:GetBucketVersioning
s3:GetEncryptionConfiguration
s3:ListBucket
s3:ListTagsForResource
s3:PutBucketObjectLockConfiguration
s3:PutBucketOwnershipControls
s3:PutBucketPolicy
s3:PutBucketPublicAccessBlock
s3:PutBucketTagging
s3:PutBucketVersioning
s3:PutEncryptionConfiguration
s3:TagResource
```

The AWS resource-type registry declares a larger conservative handler
permission superset covering optional `AWS::S3::Bucket` properties that this
template does not use. This plan does not request that superset. If
CloudFormation requires an action outside the property-scoped list above,
deployment stops at `AccessDenied`; Step 7 does not widen IAM or add an
execution role automatically.

The runtime adapter needs:

```text
s3:GetBucketLocation
s3:GetBucketObjectLockConfiguration
s3:GetBucketVersioning
s3:GetObject
s3:GetObjectRetention
s3:GetObjectVersion
s3:PutObject
s3:PutObjectRetention
```

Neither deployment nor runtime policy should grant `s3:*`, `iam:*`,
`s3:DeleteObject`, `s3:DeleteObjectVersion`, `s3:DeleteBucket`, or
`s3:BypassGovernanceRetention`. Step 7 does not change the current SSO
permission set.

## Read-only validation before the gate

```bash
python3 -m json.tool \
  infra/cloudformation/step7-s3-snapshot-authority-1a.json >/dev/null

python3 -m unittest \
  tests.test_step7_infrastructure \
  tests.test_s3_snapshot_storage -v

aws cloudformation validate-template \
  --template-body file://infra/cloudformation/step7-s3-snapshot-authority-1a.json \
  --profile aoia-admin \
  --region eu-central-1 \
  --no-cli-pager

aws s3api head-bucket \
  --bucket aioa-memory-patch-global-3f105fcd-eu-central-1 \
  --profile aoia-admin \
  --region eu-central-1 \
  --no-cli-pager

aws cloudformation describe-stacks \
  --stack-name memory-patch-step7-s3-1a \
  --query 'Stacks[0].StackStatus' \
  --output text \
  --profile aoia-admin \
  --region eu-central-1 \
  --no-cli-pager
```

`HeadBucket` is invoked once and is not retried. Its expected availability
result is a sanitized `404`. The stack-name check is also invoked once; its
expected result is a sanitized `ValidationError` stating that the named stack
does not exist.

## Exact gated deployment commands

These commands are not authorized until the user approves the displayed
Step 7 AWS write gate.

```bash
aws cloudformation create-change-set \
  --stack-name memory-patch-step7-s3-1a \
  --change-set-name step7-s3-snapshot-authority-1a \
  --change-set-type CREATE \
  --template-body file://infra/cloudformation/step7-s3-snapshot-authority-1a.json \
  --parameters \
    ParameterKey=BucketName,ParameterValue=aioa-memory-patch-global-3f105fcd-eu-central-1 \
    ParameterKey=ObjectKeyPrefix,ParameterValue=memory-patch/snapshots/v1 \
    ParameterKey=RetentionDays,ParameterValue=7 \
  --on-stack-failure DO_NOTHING \
  --description "Memory Patch Step 7 S3 snapshot authority storage only" \
  --tags \
    Key=Project,Value=memory-patch \
    Key=ProductionStep,Value=7 \
    Key=ManagedBy,Value=cloudformation \
  --profile aoia-admin \
  --region eu-central-1 \
  --no-cli-pager

aws cloudformation wait change-set-create-complete \
  --stack-name memory-patch-step7-s3-1a \
  --change-set-name step7-s3-snapshot-authority-1a \
  --profile aoia-admin \
  --region eu-central-1

aws cloudformation describe-change-set \
  --stack-name memory-patch-step7-s3-1a \
  --change-set-name step7-s3-snapshot-authority-1a \
  --profile aoia-admin \
  --region eu-central-1 \
  --no-cli-pager

aws cloudformation execute-change-set \
  --stack-name memory-patch-step7-s3-1a \
  --change-set-name step7-s3-snapshot-authority-1a \
  --profile aoia-admin \
  --region eu-central-1 \
  --no-cli-pager

aws cloudformation wait stack-create-complete \
  --stack-name memory-patch-step7-s3-1a \
  --profile aoia-admin \
  --region eu-central-1
```

The change-set description must be inspected before execution. Execution must
stop if it shows replacement, deletion, an unexpected resource type, an IAM
resource, a custom resource, a different Region, or any value outside this
plan.

## Live validation boundary

After approved deployment, read-only calls verify:

- stack outputs;
- bucket Region;
- versioning;
- Object Lock and default retention;
- encryption;
- public access block;
- ownership controls;
- tags;
- deny-only policy status.

One minimal synthetic canonical JSON snapshot may then be uploaded with an
explicit SHA-256 checksum and per-object `GOVERNANCE` retention. The exact
returned version is headed, read back, hashed, and checked for retention. It
is not deleted and governance bypass is not tested.

The payload is the fixed 88-byte file
`tests/fixtures/step7_live_validation_snapshot.json`, with SHA-256
`d1bedd6275072d01cc932af7a57d826837e27b9c7d1039bb558d4039abf819fc`.
It contains only a synthetic marker and schema version.

The repository does not install `boto3` or `botocore`. The live smoke test
therefore uses the existing AWS CLI as the transport equivalent; the actual
dependency-injected adapter is validated offline against the same request and
response contract. No SDK is installed at deployment time.

After every read-only bucket capability check passes, the only permitted
object write is:

```bash
step7_retain_until="$(
  date -u -d '7 days 5 minutes' '+%Y-%m-%dT%H:%M:%SZ'
)"

step7_version_id="$(
  aws s3api put-object \
    --bucket aioa-memory-patch-global-3f105fcd-eu-central-1 \
    --key memory-patch/snapshots/v1/global/v1/validation/d1bedd6275072d01cc932af7a57d826837e27b9c7d1039bb558d4039abf819fc.json \
    --body tests/fixtures/step7_live_validation_snapshot.json \
    --content-length 88 \
    --content-type application/json \
    --checksum-algorithm SHA256 \
    --checksum-sha256 0b7dYnUHLQHMkyr3pX2CaDfie5x9EDm7VY1AOav4Gfw= \
    --if-none-match '*' \
    --metadata canonical-sha256=d1bedd6275072d01cc932af7a57d826837e27b9c7d1039bb558d4039abf819fc,authority-status=STORAGE_EVIDENCE_ONLY,serialization-version=canonical-json-1a,synthetic=true \
    --object-lock-mode GOVERNANCE \
    --object-lock-retain-until-date "$step7_retain_until" \
    --server-side-encryption AES256 \
    --query VersionId \
    --output text \
    --profile aoia-admin \
    --region eu-central-1 \
    --no-cli-pager
)"
```

The command is executed once. A missing/null version ID, checksum mismatch,
conditional conflict, or any other error stops validation without a retry.
Subsequent `HeadObject` and `GetObject` calls name that exact version, enable
checksum mode, and use the same explicit profile and Region. The downloaded
bytes must hash to the pinned SHA-256 above. No retained object is deleted.

## Cost and rollback limitations

CloudFormation has no additional service charge, but S3 storage, versions,
requests, and retained validation bytes are billable. Retained versions cannot
be deleted until retention expires without a bypass that Step 7 forbids.

Object Lock cannot be disabled casually. Bucket replacement is not a cleanup
strategy. `DeletionPolicy` and `UpdateReplacePolicy` retain both bucket and
policy. A failed create uses `DO_NOTHING`, so an operator must inspect the
stack rather than relying on destructive rollback. No automatic lifecycle
cleanup is configured.
