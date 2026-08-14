"""Static D4 container and CloudFormation security-contract tests."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "infra/cloudformation/d4-aws-demo-runtime-1a.json"
OPERATOR_POLICY_PATH = (
    ROOT / "infra/aws/d4-deploy-operator-iam-supplement-1a.json"
)
DOCKERFILE_PATH = ROOT / "Dockerfile"
DOCKERIGNORE_PATH = ROOT / ".dockerignore"

SECRET_ENVIRONMENT_NAMES = frozenset(
    {
        "DATABASE_URL_APP",
        "DATABASE_URL_MIGRATOR",
        "OPENROUTER_API_KEY",
        "AIOA_JUDGE_ALLOWED_OIDC_SUBJECTS",
        "AIOA_DEMO_PROVIDER_TENANT_ID",
    }
)


def _template() -> dict[str, object]:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


class D4AwsDemoDeploymentTests(unittest.TestCase):
    def test_container_is_pinned_non_root_and_uses_canonical_launcher(self) -> None:
        dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
        self.assertRegex(
            dockerfile.splitlines()[0],
            r"^FROM python:3\.12\.13-slim-bookworm@sha256:[0-9a-f]{64}$",
        )
        self.assertIn('org.opencontainers.image.revision="${AIOA_SOURCE_SHA}"', dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn(
            'CMD ["python", "scripts/run_demo_runtime_1a.py", "serve"]',
            dockerfile,
        )
        self.assertNotIn("COPY . ", dockerfile)
        self.assertNotIn("ADD ", dockerfile)
        self.assertIn(
            "COPY sql/cockroachdb/migrations/ ./sql/cockroachdb/migrations/",
            dockerfile,
        )
        self.assertNotIn("COPY sql/ ./sql/", dockerfile)
        self.assertNotIn("OPENROUTER_API_KEY=", dockerfile)
        self.assertNotIn("DATABASE_URL_APP=", dockerfile)

    def test_build_context_excludes_credentials_and_non_runtime_bulk(self) -> None:
        patterns = set(DOCKERIGNORE_PATH.read_text(encoding="utf-8").splitlines())
        self.assertIn("**", patterns)
        for required in {
            "!AGENTS.md",
            "!requirements-runtime.txt",
            "!requirements-ui.txt",
            "!src/**",
            "!config/**",
            "!schemas/**",
            "!sql/cockroachdb/migrations/**",
        }:
            self.assertIn(required, patterns)
        self.assertIn("config/external-data.env.example", patterns)
        self.assertIn("!tests/fixtures/step38_german_law_cases.json", patterns)
        self.assertIn("!scripts/run_demo_runtime_1a.py", patterns)
        self.assertIn("!scripts/run_cockroachdb_migrations.py", patterns)

    def test_ecr_is_immutable_scanned_encrypted_and_bounded(self) -> None:
        repository = _template()["Resources"]["DemoImageRepository"]
        properties = repository["Properties"]
        self.assertEqual(properties["ImageTagMutability"], "IMMUTABLE")
        self.assertEqual(properties["ImageScanningConfiguration"], {"ScanOnPush": True})
        self.assertEqual(
            properties["EncryptionConfiguration"], {"EncryptionType": "AES256"}
        )
        policy = json.loads(properties["LifecyclePolicy"]["LifecyclePolicyText"])
        self.assertEqual(policy["rules"][0]["selection"]["countNumber"], 5)
        self.assertEqual(repository["DeletionPolicy"], "Retain")

    def test_express_service_has_one_bounded_fargate_task(self) -> None:
        template = _template()
        service = template["Resources"]["DemoService"]
        properties = service["Properties"]
        self.assertEqual(service["Type"], "AWS::ECS::ExpressGatewayService")
        self.assertEqual(properties["Cpu"], "1024")
        self.assertEqual(properties["Memory"], "3072")
        self.assertEqual(properties["HealthCheckPath"], "/health/ready")
        self.assertEqual(
            properties["ScalingTarget"],
            {
                "AutoScalingMetric": "AVERAGE_CPU",
                "AutoScalingTargetValue": 60,
                "MaxTaskCount": 1,
                "MinTaskCount": 1,
            },
        )
        container = properties["PrimaryContainer"]
        self.assertEqual(container["ContainerPort"], 8000)
        self.assertEqual(
            container["Command"],
            ["python", "scripts/run_demo_runtime_1a.py", "serve"],
        )
        self.assertNotIn("TaskRoleArn", properties)
        self.assertNotIn("NetworkConfiguration", properties)
        self.assertEqual(
            template["Parameters"]["ServiceName"]["AllowedValues"],
            ["memory-patch-aioa-demo-1a"],
        )
        self.assertIn(
            "@sha256:[0-9a-f]{64}",
            template["Parameters"]["ImageUri"]["AllowedPattern"],
        )

    def test_hosted_runtime_configuration_preserves_closed_bounds(self) -> None:
        container = _template()["Resources"]["DemoService"]["Properties"][
            "PrimaryContainer"
        ]
        environment = {
            entry["Name"]: entry["Value"] for entry in container["Environment"]
        }
        expected = {
            "AIOA_RUNTIME_MODE": "HOSTED_DEMO",
            "AIOA_RUNTIME_BIND_HOST": "0.0.0.0",
            "AIOA_RUNTIME_PORT": "8000",
            "AIOA_DEMO_LEGACY_MODE_ENABLED": "1",
            "AIOA_DB_ALLOW_INSECURE_LOCAL": "0",
            "AIOA_DB_POOL_MIN": "1",
            "AIOA_DB_POOL_MAX": "4",
            "AIOA_DEMO_PROVIDER_MAX_CALLS_TOTAL": "32",
            "AIOA_DEMO_PROVIDER_MAX_CALLS_PER_OWNER": "12",
            "AIOA_DEMO_PROVIDER_MAX_CALLS_PER_SESSION": "10",
            "AIOA_DEMO_PROVIDER_MAX_CALLS_PER_REQUEST": "8",
            "AIOA_DEMO_PROVIDER_MAX_CONCURRENT_CALLS": "1",
            "AIOA_DEMO_PROVIDER_MAX_QUEUED_CALLS": "2",
            "AIOA_DEMO_PROVIDER_MAX_INPUT_BYTES": "24576",
            "AIOA_DEMO_PROVIDER_MAX_OUTPUT_TOKENS": "1024",
            "AIOA_DEMO_PROVIDER_TIMEOUT_SECONDS": "45",
        }
        for name, value in expected.items():
            self.assertEqual(environment[name], value)
        self.assertTrue(SECRET_ENVIRONMENT_NAMES.isdisjoint(environment))
        self.assertEqual(
            environment["AIOA_RUNTIME_PUBLIC_ORIGIN"],
            {"Fn::Sub": "https://${ServiceName}.ecs.${AWS::Region}.on.aws"},
        )

    def test_only_exact_runtime_secret_is_read_by_execution_role(self) -> None:
        template = _template()
        key = template["Resources"]["DemoRuntimeSecretsKey"]
        self.assertEqual(key["Type"], "AWS::KMS::Key")
        self.assertTrue(key["Properties"]["EnableKeyRotation"])
        self.assertEqual(key["DeletionPolicy"], "Retain")
        self.assertEqual(key["UpdateReplacePolicy"], "Retain")
        secret = template["Resources"]["DemoRuntimeSecret"]
        self.assertEqual(secret["Type"], "AWS::SecretsManager::Secret")
        self.assertEqual(secret["DeletionPolicy"], "Retain")
        self.assertEqual(secret["UpdateReplacePolicy"], "Retain")
        self.assertEqual(
            secret["Properties"]["KmsKeyId"],
            {"Fn::GetAtt": ["DemoRuntimeSecretsKey", "Arn"]},
        )
        self.assertNotIn("SecretString", secret["Properties"])
        self.assertNotIn("GenerateSecretString", secret["Properties"])
        role = template["Resources"]["DemoTaskExecutionRole"]["Properties"]
        statements = role["Policies"][0]["PolicyDocument"]["Statement"]
        self.assertEqual(len(statements), 2)
        self.assertEqual(
            statements[0]["Action"],
            ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"],
        )
        self.assertEqual(statements[0]["Resource"], {"Ref": "DemoRuntimeSecret"})
        self.assertEqual(
            statements[0]["Condition"],
            {"Bool": {"aws:SecureTransport": "true"}},
        )
        self.assertEqual(
            statements[1]["Action"], ["kms:Decrypt", "kms:DescribeKey"]
        )
        self.assertEqual(
            statements[1]["Resource"],
            {"Fn::GetAtt": ["DemoRuntimeSecretsKey", "Arn"]},
        )
        self.assertEqual(
            statements[1]["Condition"]["StringEquals"]["kms:ViaService"],
            {"Fn::Sub": "secretsmanager.${AWS::Region}.${AWS::URLSuffix}"},
        )
        container = template["Resources"]["DemoService"]["Properties"][
            "PrimaryContainer"
        ]
        secret_names = {entry["Name"] for entry in container["Secrets"]}
        self.assertEqual(secret_names, SECRET_ENVIRONMENT_NAMES)
        for entry in container["Secrets"]:
            value_from = entry["ValueFrom"]["Fn::Sub"]
            self.assertEqual(
                value_from,
                "${DemoRuntimeSecret}:" + entry["Name"] + "::",
            )

    def test_iam_has_no_application_admin_or_aws_business_role(self) -> None:
        template = _template()
        text = TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("AdministratorAccess", text)
        self.assertNotIn('"Action": "*"', text)
        for resource in template["Resources"].values():
            if resource["Type"] == "AWS::IAM::Role":
                self.assertNotIn('"Resource": "*"', json.dumps(resource))
        self.assertNotIn("AWS::IAM::User", text)
        self.assertNotIn("AWS::IAM::AccessKey", text)
        self.assertNotIn("DemoTaskRole", template["Resources"])
        self.assertEqual(
            template["Resources"]["DemoInfrastructureRole"]["Properties"][
                "ManagedPolicyArns"
            ],
            [
                "arn:aws:iam::aws:policy/service-role/"
                "AmazonECSInfrastructureRoleforExpressGatewayServices"
            ],
        )

    def test_deploy_operator_iam_supplement_is_d4_role_scoped(self) -> None:
        policy = json.loads(OPERATOR_POLICY_PATH.read_text(encoding="utf-8"))
        text = OPERATOR_POLICY_PATH.read_text(encoding="utf-8")
        self.assertNotIn("AdministratorAccess", text)
        self.assertNotIn('"Action": "*"', text)
        self.assertNotIn('"Resource": "*"', text)
        expected_roles = {
            "arn:aws:iam::787391403107:role/"
            "memory-patch-aioa-demo-1a-execution",
            "arn:aws:iam::787391403107:role/"
            "memory-patch-aioa-demo-1a-infrastructure",
            "arn:aws:iam::787391403107:role/"
            "memory-patch-aioa-demo-1a-oidc-claims",
        }
        statements = policy["Statement"]
        self.assertEqual(len(statements), 4)
        self.assertEqual(set(statements[0]["Resource"]), expected_roles)
        self.assertEqual(statements[1]["Action"], "iam:PassRole")
        self.assertEqual(
            statements[1]["Resource"],
            "arn:aws:iam::787391403107:role/"
            "memory-patch-aioa-demo-1a-execution",
        )
        self.assertEqual(
            statements[1]["Condition"]["StringEquals"]["iam:PassedToService"],
            "ecs-tasks.amazonaws.com",
        )
        self.assertEqual(statements[2]["Action"], "iam:PassRole")
        self.assertEqual(
            statements[2]["Resource"],
            "arn:aws:iam::787391403107:role/"
            "memory-patch-aioa-demo-1a-infrastructure",
        )
        self.assertEqual(
            statements[2]["Condition"]["StringEquals"]["iam:PassedToService"],
            "ecs.amazonaws.com",
        )
        self.assertEqual(statements[3]["Action"], "iam:PassRole")
        self.assertEqual(
            statements[3]["Resource"],
            "arn:aws:iam::787391403107:role/"
            "memory-patch-aioa-demo-1a-oidc-claims",
        )
        self.assertEqual(
            statements[3]["Condition"]["StringEquals"]["iam:PassedToService"],
            "lambda.amazonaws.com",
        )

    def test_task_execution_trust_is_account_and_ecs_source_scoped(self) -> None:
        trust = _template()["Resources"]["DemoTaskExecutionRole"]["Properties"][
            "AssumeRolePolicyDocument"
        ]["Statement"][0]
        self.assertEqual(trust["Principal"], {"Service": "ecs-tasks.amazonaws.com"})
        self.assertEqual(
            trust["Condition"]["StringEquals"]["aws:SourceAccount"],
            {"Ref": "AWS::AccountId"},
        )
        self.assertEqual(
            trust["Condition"]["ArnLike"]["aws:SourceArn"],
            {
                "Fn::Sub": (
                    "arn:${AWS::Partition}:ecs:${AWS::Region}:${AWS::AccountId}:*"
                )
            },
        )

    def test_public_origin_and_oidc_callback_are_exact(self) -> None:
        outputs = _template()["Outputs"]
        self.assertEqual(
            outputs["PublicOrigin"]["Value"]["Fn::Sub"],
            "https://${ServiceName}.ecs.${AWS::Region}.on.aws",
        )
        self.assertEqual(
            outputs["OidcCallbackUrl"]["Value"]["Fn::Sub"],
            "https://${ServiceName}.ecs.${AWS::Region}.on.aws/memory/oidc/callback",
        )

    def test_cognito_is_lite_admin_only_public_pkce_code_flow(self) -> None:
        template = _template()
        resources = template["Resources"]
        pool = resources["DemoJudgeUserPool"]
        self.assertEqual(pool["Type"], "AWS::Cognito::UserPool")
        self.assertEqual(pool["DeletionPolicy"], "Retain")
        properties = pool["Properties"]
        self.assertEqual(properties["UserPoolTier"], "LITE")
        self.assertEqual(properties["DeletionProtection"], "ACTIVE")
        self.assertEqual(
            properties["AdminCreateUserConfig"], {"AllowAdminCreateUserOnly": True}
        )
        self.assertEqual(properties["MfaConfiguration"], "OFF")
        self.assertNotIn(
            "PasswordHistorySize", properties["Policies"]["PasswordPolicy"]
        )
        self.assertEqual(
            properties["LambdaConfig"]["PreTokenGenerationConfig"][
                "LambdaVersion"
            ],
            "V1_0",
        )

        client = resources["DemoJudgeUserPoolClient"]["Properties"]
        self.assertFalse(client["GenerateSecret"])
        self.assertTrue(client["AllowedOAuthFlowsUserPoolClient"])
        self.assertEqual(client["AllowedOAuthFlows"], ["code"])
        self.assertEqual(client["AllowedOAuthScopes"], ["openid", "profile"])
        self.assertEqual(client["SupportedIdentityProviders"], ["COGNITO"])
        self.assertEqual(
            client["CallbackURLs"],
            [
                {
                    "Fn::Sub": (
                        "https://${ServiceName}.ecs.${AWS::Region}.on.aws/"
                        "memory/oidc/callback"
                    )
                }
            ],
        )
        self.assertEqual(
            resources["DemoJudgeUserPoolDomain"]["Properties"][
                "ManagedLoginVersion"
            ],
            1,
        )

    def test_oidc_claims_lambda_is_fixed_bounded_and_non_secret_bearing(self) -> None:
        template = _template()
        resources = template["Resources"]
        function = resources["DemoOidcClaimsFunction"]["Properties"]
        self.assertEqual(function["Runtime"], "python3.12")
        self.assertEqual(function["Timeout"], 3)
        self.assertEqual(function["MemorySize"], 128)
        self.assertNotIn("ReservedConcurrentExecutions", function)
        code = function["Code"]["ZipFile"]
        self.assertIn('event.get("version") != "1"', code)
        self.assertIn('"tenant_id": "memory-patch-aioa-demo-1a"', code)
        self.assertIn('"owner_user_id": subject', code)
        self.assertNotIn("OPENROUTER_API_KEY", code)
        self.assertNotIn("DATABASE_URL", code)
        self.assertNotIn('attributes["sub"] =', code)
        namespace: dict[str, object] = {}
        exec(code, namespace)
        event = {
            "version": "1",
            "request": {
                "userAttributes": {"sub": "immutable-cognito-sub"},
                "groupConfiguration": {"groupsToOverride": ["judge"]},
            },
            "response": {},
        }
        result = namespace["handler"](event, None)
        claims = result["response"]["claimsOverrideDetails"]
        self.assertEqual(
            claims["claimsToAddOrOverride"],
            {
                "tenant_id": "memory-patch-aioa-demo-1a",
                "owner_user_id": "immutable-cognito-sub",
            },
        )
        self.assertEqual(
            result["request"]["userAttributes"]["sub"], "immutable-cognito-sub"
        )

        permission = resources["DemoOidcClaimsPermission"]["Properties"]
        self.assertEqual(permission["Principal"], "cognito-idp.amazonaws.com")
        self.assertEqual(permission["SourceAccount"], {"Ref": "AWS::AccountId"})
        role = resources["DemoOidcClaimsRole"]["Properties"]
        actions = role["Policies"][0]["PolicyDocument"]["Statement"][0]["Action"]
        self.assertEqual(actions, ["logs:CreateLogStream", "logs:PutLogEvents"])

    def test_runtime_uses_the_stack_owned_oidc_identity(self) -> None:
        template = _template()
        self.assertNotIn("OidcIssuer", template["Parameters"])
        self.assertNotIn("OidcClientId", template["Parameters"])
        environment = {
            entry["Name"]: entry["Value"]
            for entry in template["Resources"]["DemoService"]["Properties"][
                "PrimaryContainer"
            ]["Environment"]
        }
        self.assertEqual(
            environment["AIOA_OIDC_ISSUER"],
            {"Fn::GetAtt": ["DemoJudgeUserPool", "ProviderURL"]},
        )
        self.assertEqual(
            environment["AIOA_OIDC_CLIENT_ID"], {"Ref": "DemoJudgeUserPoolClient"}
        )

    def test_template_contains_no_literal_credentials(self) -> None:
        text = TEMPLATE_PATH.read_text(encoding="utf-8")
        forbidden = (
            r"postgres(?:ql)?://[^\s\"']+",
            r"sk-or-v1-[A-Za-z0-9_-]+",
            r"AKIA[0-9A-Z]{16}",
            r"ASIA[0-9A-Z]{16}",
            r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY",
        )
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, text))


if __name__ == "__main__":
    unittest.main()
