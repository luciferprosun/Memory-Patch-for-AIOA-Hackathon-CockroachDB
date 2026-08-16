# AWS Unified AIOA Demo Runtime 1A

This document records the repository's D4 runtime design. The later integrated
AOIA-Core runtime used for the recorded and deployed jury demo is identified in
the [final release manifest](../submission/FINAL_HACKATHON_RELEASE_MANIFEST.md).
The public-origin values below have been updated from the pre-deployment
prediction to the endpoint actually assigned by AWS.

## Decision

D4 uses one Amazon ECS Express Mode service in `eu-central-1`. AWS App Runner
is not selected because AWS stopped accepting new App Runner customers and
recommends ECS Express Mode for the same managed-container use case. The
account has no existing App Runner service. ECS Express Mode supplies Fargate,
an Application Load Balancer, a managed certificate, a stable HTTPS endpoint,
security groups and bounded autoscaling without adding Kubernetes or a second
application.

Authoritative AWS references:

- [App Runner availability change](https://docs.aws.amazon.com/apprunner/latest/dg/apprunner-availability-change.html)
- [ECS Express Mode overview](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-overview.html)
- [ECS Express Mode CloudFormation resource](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ecs-expressgatewayservice.html)
- [Fargate CPU and memory combinations](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html)

## Frozen architecture

```text
Judge browser
  -> AWS-managed HTTPS endpoint
  -> ECS Express managed Application Load Balancer
  -> one Fargate task, one Python process, one Uvicorn worker
  -> aioa_memory_kernel.demo_runtime.asgi:app
     -> OIDC Authorization Code + PKCE
     -> CockroachOwnerSessionStore
     -> Unified AIOA cockpit
        -> Memory Patch Current, authoritative
        -> Critical Prompt Loop Legacy, enabled archival metadata view
     -> CockroachDB over sslmode=verify-full
     -> GuardedProviderAdapter and CockroachProviderGuardLedger
     -> OpenRouter moonshotai/kimi-k2
```

There is one ASGI app, one login boundary, one durable session cookie, one
CockroachDB authority design and one provider guard. The task receives no AWS
application role because application code does not call AWS APIs. The ECS task
execution role can pull the exact ECR image, write the exact log group and read
one exact runtime-secret ARN. ECS receives its own infrastructure role only for
the managed Express resources.

## Resource bounds

| Boundary | Frozen value |
| --- | --- |
| ECS tasks | minimum 1, maximum 1 |
| Fargate CPU | 1024 CPU units, 1 vCPU |
| Fargate memory | 3072 MiB |
| Web workers | 1 |
| DB application pool | 1 through 4 |
| Provider concurrency | 1 |
| Provider queue | 2 |
| Provider call ceiling | 32 per durable budget epoch |
| Log retention | 7 days |
| ECR retention | five newest images |
| Legacy mode | archival metadata view enabled; live/replay calls unavailable |
| Legacy live calls | 0 |

The 3072 MiB task matches the Step 40 runtime peak gate and is a supported
Fargate pairing with 1 vCPU. Public default subnets provide outbound HTTPS, so
no NAT Gateway is created. Express Mode does create a managed Application Load
Balancer; that cost is explicit and is the reason this is the allowed ECS
fallback rather than the original App Runner preference.

## Immutable image boundary

The image uses the official CPython `3.12.13-slim-bookworm` multi-platform
manifest pinned by SHA-256. A build argument supplies the exact source commit
for the OCI revision label. Runtime code is read-only and runs as UID/GID
`10001:10001`. The image includes only the canonical runtime source, config,
schemas, migrations, two runtime scripts and the exact guided-case fixture.
It excludes Git data, local environments, documentation archives, caches,
credentials and model weights.

ECR uses immutable tags and scan-on-push. Deployment uses the repository URI
plus image digest, never a mutable `latest` reference.

## Configuration and secret boundary

Non-secret configuration enters the task as typed environment variables. AWS
assigned this final public origin to the frozen jury service:

```text
https://me-50f4749c79c34254a788d95160d08a6c.ecs.eu-central-1.on.aws
```

The exact callback is:

```text
https://me-50f4749c79c34254a788d95160d08a6c.ecs.eu-central-1.on.aws/memory/oidc/callback
```

One retained Secrets Manager JSON object is created empty by CloudFormation,
encrypted by a dedicated retained KMS key with annual automatic key-material
rotation, and populated out of band. It supplies only these keys through ECS
secret references:

- `DATABASE_URL_APP`
- `DATABASE_URL_MIGRATOR`
- `OPENROUTER_API_KEY`
- `AIOA_JUDGE_ALLOWED_OIDC_SUBJECTS`
- `AIOA_DEMO_PROVIDER_TENANT_ID`

CloudFormation owns only the empty secret metadata and receives no secret
value. Secret values never enter a template parameter, image, Git diff, log,
browser response or D4 evidence.
The database URLs must retain distinct principals and `sslmode=verify-full`.
The OIDC issuer and public client ID are non-secret configuration; this runtime
does not define an OIDC client-secret input.

CloudTrail Event history supplies the account's immutable 90-day regional
management-event record for D4, including Secrets Manager operations. The demo
stack intentionally does not create an account-wide persistent trail, log
archive or alerting subsystem; that would be a separate account-governance
decision rather than application runtime authority.

## Startup, readiness and shutdown

The container invokes only:

```text
python scripts/run_demo_runtime_1a.py serve
```

That launcher validates hosted configuration, applies or checks all 19
canonical migrations with the migration credential, drops migration authority,
creates the normal pool and then starts the ASGI lifespan. Express traffic is
admitted through `/health/ready`; `/health/live` remains the minimal process
probe. Neither endpoint makes a provider call. Readiness remains non-200 until
the database, durable sessions, OIDC configuration, provider identity and
durable budget guard are safe.

Shutdown first removes readiness, then closes bounded work, sessions and DB
resources using the R7 lifecycle. ECS Express may overlap service revisions,
but the canonical migration runner is checksum-bound and replay-safe. The task
count remains one after the revision transition.

## Authority preservation

- Browser database, provider and Commit Helper credentials: none.
- Normal app role and migration role: distinct.
- Admin or master database fallback: none.
- Provider database, review, approval, commit or activation authority: none.
- Critical Prompt Loop canonical evidence or Personal Memory authority: none.
- Test authentication or in-memory hosted session fallback: none.
- Provider fallback or model substitution: none.
- Paid call from health, page load, mode selection or legacy archival view:
  none.

The full hosted Golden Path is deliberately deferred to D5.
