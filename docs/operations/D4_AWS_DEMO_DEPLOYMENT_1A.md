# D4 AWS Demo Deployment 1A

## Scope and no-go boundary

This runbook deploys one source-controlled Unified AIOA cockpit to ECS Express
Mode. It does not run the paid Golden Path, enable legacy live execution,
replace CockroachDB, change the provider/model or start D5.

Stop before mutation if any of these is true:

- the AWS principal is root;
- `main` is dirty or differs from `origin/main`;
- the source SHA is not committed;
- the approved `aws-secrets-manager` workflow is unavailable;
- either CockroachDB URL lacks the required purpose-specific principal or
  `sslmode=verify-full`;
- the OIDC callback is not registered exactly;
- the provider budget epoch is not deliberately armed;
- the image is not pinned by ECR digest;
- a change set contains an unrelated resource or unbounded task count.

## Fixed identities

| Item | Value |
| --- | --- |
| AWS profile | `aoia-admin` |
| Region | `eu-central-1` |
| Stack | `memory-patch-aioa-demo-1a` |
| Service | `memory-patch-aioa-demo-1a` |
| ECR repository | `memory-patch-aioa-demo-1a` |
| CloudFormation | `infra/cloudformation/d4-aws-demo-runtime-1a.json` |
| ASGI app | `aioa_memory_kernel.demo_runtime.asgi:app` |
| Launcher | `python scripts/run_demo_runtime_1a.py serve` |
| Public origin | `https://memory-patch-aioa-demo-1a.ecs.eu-central-1.on.aws` |
| OIDC callback | `https://memory-patch-aioa-demo-1a.ecs.eu-central-1.on.aws/memory/oidc/callback` |
| Cockpit | `/memory/demo` |
| Liveness | `/health/live` |
| Readiness | `/health/ready` |

## 1. Repository and AWS preflight

Use the SSO profile explicitly. Never use the default profile for this task.

```bash
git status --short
git fetch origin
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count HEAD...origin/main

env -u GOOGLE_OAUTH_CLIENT_CONFIG_JSON \
  AWS_PROFILE=aoia-admin AWS_REGION=eu-central-1 AWS_PAGER='' \
  aws sts get-caller-identity
```

Require a clean worktree, `HEAD == origin/main`, divergence `0 0`, and an
assumed SSO role ARN rather than the account root ARN.

The `LuciferSOL` permission set currently attaches `PowerUserAccess`. That
managed policy intentionally excludes IAM role management and `iam:PassRole`.
Before the service phase, an account administrator must review and apply the
exact supplement in
`infra/aws/d4-deploy-operator-iam-supplement-1a.json` to the `LuciferSOL`
permission set, provision it to account `787391403107`, and start a fresh SSO
session. The supplement is limited to the three named D4 roles: two service
roles passed only to ECS and one claims-trigger role passed only to Lambda. Do
not attach `AdministratorAccess` and do not widen the role-name resources.

Run provider-free predeployment validation:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python -m unittest tests.test_d4_aws_demo_deployment -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. \
  .venv/bin/python scripts/run_d4_aws_demo_deployment_validation.py

env -u GOOGLE_OAUTH_CLIENT_CONFIG_JSON \
  AWS_PROFILE=aoia-admin AWS_REGION=eu-central-1 AWS_PAGER='' \
  aws cloudformation validate-template \
    --template-body file://infra/cloudformation/d4-aws-demo-runtime-1a.json

git diff --check
```

## 2. Prepare the base change set

Freeze the committed SHA in a task-specific variable:

```bash
AIOA_D4_SOURCE_SHA="$(git rev-parse HEAD)"
```

Create or update a CloudFormation change set with `DeployApplication=false`.
This phase owns the immutable ECR repository, dedicated retained rotating KMS
key, empty retained Secrets Manager object, and the admin-only Cognito Lite
pool with its public code-flow client, managed domain, and bounded V1 claims
trigger. A change set must be reviewed and explicitly approved before
execution.

```bash
env -u GOOGLE_OAUTH_CLIENT_CONFIG_JSON \
  AWS_PROFILE=aoia-admin AWS_REGION=eu-central-1 AWS_PAGER='' \
  aws cloudformation create-change-set \
    --stack-name memory-patch-aioa-demo-1a \
    --change-set-name d4-ecr-base-1a \
    --change-set-type CREATE \
    --template-body file://infra/cloudformation/d4-aws-demo-runtime-1a.json \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameters \
      ParameterKey=SourceSha,ParameterValue="$AIOA_D4_SOURCE_SHA" \
      ParameterKey=DeployApplication,ParameterValue=false
```

Inspect with `describe-change-set`. Execute only after explicit approval:

```bash
env -u GOOGLE_OAUTH_CLIENT_CONFIG_JSON \
  AWS_PROFILE=aoia-admin AWS_REGION=eu-central-1 AWS_PAGER='' \
  aws cloudformation execute-change-set \
    --stack-name memory-patch-aioa-demo-1a \
    --change-set-name d4-ecr-base-1a
```

## 3. Build and push the immutable image

Podman is the canonical local OCI builder for D4. Build only from the clean
committed SHA. Do not use build arguments for credentials.

```bash
AIOA_D4_ACCOUNT_ID="787391403107"
AIOA_D4_ECR_HOST="${AIOA_D4_ACCOUNT_ID}.dkr.ecr.eu-central-1.amazonaws.com"
AIOA_D4_IMAGE_TAG="$AIOA_D4_SOURCE_SHA"
AIOA_D4_IMAGE="${AIOA_D4_ECR_HOST}/memory-patch-aioa-demo-1a:${AIOA_D4_IMAGE_TAG}"

podman build --pull=always \
  --build-arg AIOA_SOURCE_SHA="$AIOA_D4_SOURCE_SHA" \
  --tag "$AIOA_D4_IMAGE" .
```

Authenticate through the approved AWS CLI credential flow without printing or
persisting the transient registry password, then push the exact SHA tag:

```bash
env -u GOOGLE_OAUTH_CLIENT_CONFIG_JSON \
  AWS_PROFILE=aoia-admin AWS_REGION=eu-central-1 AWS_PAGER='' \
  aws ecr get-login-password \
  | podman login --username AWS --password-stdin "$AIOA_D4_ECR_HOST"

podman push "$AIOA_D4_IMAGE"
```

Query ECR metadata and freeze the returned `sha256:` image digest. The service
parameter must use `repository@sha256:digest`, not the tag and not `latest`.

Inspect image history before push and require no secret-bearing build argument,
environment layer or copied local file. Wait for scan completion and evaluate
critical/high findings before service creation.

## 4. Populate the runtime secret without exposing values

CloudFormation creates the runtime secret with no value. Populate it outside
the agent context through the AWS console using these exact keys only:

```text
DATABASE_URL_APP
DATABASE_URL_MIGRATOR
OPENROUTER_API_KEY
AIOA_JUDGE_ALLOWED_OIDC_SUBJECTS
AIOA_DEMO_PROVIDER_TENANT_ID
```

The stack creates a dedicated KMS key and enables annual automatic rotation of
its key material. The ECS execution role may decrypt that exact key only
through Secrets Manager. Never call `get-secret-value` or
`batch-get-secret-value`. Record only the versionless secret and key ARNs. Do
not put values in CloudFormation, parameters files, shell history, evidence or
logs. Automatic rotation of the composite secret value is not enabled because
the bundle contains independently managed external CockroachDB, OpenRouter and
identity values, and D4 does not authorize a custom rotation Lambda. Rotate
each credential at its upstream source and deliberately replace the JSON
bundle.

The account currently has no customer-created CloudTrail trail. AWS CloudTrail
Event history still records regional management events for 90 days, including
Secrets Manager API activity. D4 uses that bounded audit source and does not
silently add an account-wide S3/CloudWatch trail. A persistent multi-region
trail and alerting policy is an explicit post-hackathon operator decision.
Inspect only event metadata, never secret values:

```bash
env -u GOOGLE_OAUTH_CLIENT_CONFIG_JSON \
  AWS_PROFILE=aoia-admin AWS_REGION=eu-central-1 AWS_PAGER='' \
  aws cloudtrail lookup-events \
    --lookup-attributes \
      AttributeKey=EventSource,AttributeValue=secretsmanager.amazonaws.com \
    --max-results 20
```

CloudFormation registers this exact callback on the public Cognito client:

```text
https://memory-patch-aioa-demo-1a.ecs.eu-central-1.on.aws/memory/oidc/callback
```

The stack outputs the exact non-secret OIDC issuer and public client ID and
binds them directly into the service. Do not invent or pass an OIDC client
secret because the application has no such input. Self-registration is off;
create only the approved judge user through the Cognito admin API.

## 5. Prepare and execute the service change set

Use a protected, untracked parameter file or direct parameter overrides. It
may contain only the image digest URI, budget epoch and source SHA. It must
contain no secret value.

The update must set:

```text
DeployApplication=true
ImageUri=<repository-uri>@sha256:<digest>
ProviderBudgetEpoch=<deliberately-armed-epoch>
SourceSha=<exact-committed-sha>
```

Create an `UPDATE` change set with `CAPABILITY_NAMED_IAM`, inspect every
resource and IAM change, and request explicit approval. Only then execute it.
Expected service-phase resources are one runtime log group, one task execution
role, one ECS infrastructure role and one ECS Express service. The Cognito
pool, client, domain and claims trigger already belong to the base phase. ECS
Express manages the one Fargate service, ALB, certificate, target group,
security groups and scaling policy. No NAT Gateway, database, Redis,
Kubernetes or task role is expected.

Monitor with:

```bash
env -u GOOGLE_OAUTH_CLIENT_CONFIG_JSON \
  AWS_PROFILE=aoia-admin AWS_REGION=eu-central-1 AWS_PAGER='' \
  aws ecs monitor-express-gateway-service \
    --service-arn <exact-service-arn> \
    --monitor-mode RESOURCE
```

## 6. D4 no-cost smoke

Do not submit a jury run. D4 expects zero paid provider calls.

```bash
curl --fail --silent --show-error \
  https://memory-patch-aioa-demo-1a.ecs.eu-central-1.on.aws/health/live

curl --fail --silent --show-error \
  https://memory-patch-aioa-demo-1a.ecs.eu-central-1.on.aws/health/ready
```

Require HTTP 200, bounded JSON, security headers and no secret. Confirm API
docs are disabled, unauthenticated `/memory` and `/memory/demo` follow the
existing auth contract, and no unauthenticated request can reach a provider
call. Open the login route only after the callback is exact. Complete one
interactive allowed-judge login, verify Secure/HttpOnly/SameSite cookie flags,
then log out and confirm revocation. Do not copy OIDC codes or tokens into the
repository or report.

Inspect only sanitized CloudWatch events for startup, migration state,
readiness and shutdown. Do not retrieve or print secret values, raw DSNs,
provider keys, OIDC tokens or private Personal Memory content.

## 7. Rollback

Freeze the last known-good image digest and service revision in D4 evidence.
To roll back, create an `UPDATE` change set that changes only `ImageUri` and
`SourceSha` to that exact prior pair while retaining every security and budget
parameter. Review and approve the change set, execute it, then require
`/health/ready` HTTP 200. Never roll back to the historical Critical Prompt
server or an unguarded provider path.

## 8. Teardown

After the hackathon, and only with explicit destructive approval:

```bash
env -u GOOGLE_OAUTH_CLIENT_CONFIG_JSON \
  AWS_PROFILE=aoia-admin AWS_REGION=eu-central-1 AWS_PAGER='' \
  aws cloudformation delete-stack \
    --stack-name memory-patch-aioa-demo-1a
```

The template retains the ECR repository, images, runtime secret and dedicated
KMS key after stack deletion. The external CockroachDB cluster/database and Git
repository are not stack resources and remain untouched. Delete retained
images, the retained secret or the retained KMS key only through a separate
exact-target approval. Confirm no ECS service, ALB, target group, managed
certificate, scaling policy, security group or log group remains from the
deleted stack.

## Cost posture

Ongoing D4 cost sources are one Fargate task at 1 vCPU/3 GiB, one managed ALB,
small ECR storage, seven-day CloudWatch logs, one Secrets Manager secret and
one customer-managed KMS key.
The task count is fixed at one. There is no NAT Gateway, duplicate environment,
AWS database, Redis, custom domain or extra always-on worker. Teardown is the
cost stop mechanism after the demo window.

The AWS Price List API snapshot taken on 2026-08-14 for EU Frankfurt returned
`$0.04656` per Linux x86 Fargate vCPU-hour, `$0.00511` per Fargate GB-hour,
`$0.027` per Application Load Balancer-hour and `$0.008` per ALB LCU-hour.
A deterministic decimal calculation gives approximately `$0.06189/hour` for
the task and `$0.08889/hour` for task plus the fixed ALB charge. At the AWS
pricing convention of 730 hours, that fixed subtotal is approximately
`$64.89/month`; one continuously consumed LCU would make it approximately
`$70.73/month`. ECR, logs, Secrets Manager, data transfer, taxes, traffic-based
LCUs and any account-specific pricing are additional. These figures are a
public on-demand estimate, not a billing guarantee; recheck the AWS Pricing
Calculator before leaving the service running beyond the demo window.
