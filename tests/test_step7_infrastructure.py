"""Offline assertions for the bounded Step 7 CloudFormation template."""

from __future__ import annotations

import hashlib
import json
import re
import unittest

from tests._support import REPOSITORY_ROOT


TEMPLATE_PATH = (
    REPOSITORY_ROOT
    / "infra"
    / "cloudformation"
    / "step7-s3-snapshot-authority-1a.json"
)
LIVE_VALIDATION_PAYLOAD = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "step7_live_validation_snapshot.json"
)


def load_template() -> dict[str, object]:
    value = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("template root must be an object")
    return value


class Step7InfrastructureTests(unittest.TestCase):
    def test_live_validation_payload_is_fixed_minimal_and_synthetic(
        self,
    ) -> None:
        raw = LIVE_VALIDATION_PAYLOAD.read_bytes()
        self.assertEqual(
            raw,
            (
                b'{"kind":"memory-patch-step7-live-validation",'
                b'"schema_version":"1.0.0","synthetic":true}\n'
            ),
        )
        self.assertEqual(len(raw), 88)
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            "d1bedd6275072d01cc932af7a57d8268"
            "37e27b9c7d1039bb558d4039abf819fc",
        )

    def test_template_is_deterministic_canonical_json(self) -> None:
        raw = TEMPLATE_PATH.read_bytes()
        value = json.loads(raw)
        expected = (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(raw, expected)
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            hashlib.sha256(expected).hexdigest(),
        )

    def test_exactly_one_step7_bucket_and_no_unrelated_service(self) -> None:
        resources = load_template()["Resources"]
        self.assertIsInstance(resources, dict)
        resource_types = [
            resource["Type"]
            for resource in resources.values()  # type: ignore[union-attr]
        ]
        self.assertEqual(resource_types.count("AWS::S3::Bucket"), 1)
        self.assertEqual(
            set(resource_types),
            {"AWS::S3::Bucket", "AWS::S3::BucketPolicy"},
        )

    def test_object_lock_versioning_encryption_and_ownership_are_safe(
        self,
    ) -> None:
        bucket = load_template()["Resources"]["SnapshotBucket"]  # type: ignore[index]
        properties = bucket["Properties"]
        self.assertIs(properties["ObjectLockEnabled"], True)
        self.assertEqual(
            properties["ObjectLockConfiguration"],
            {
                "ObjectLockEnabled": "Enabled",
                "Rule": {
                    "DefaultRetention": {
                        "Days": {"Ref": "RetentionDays"},
                        "Mode": "GOVERNANCE",
                    }
                },
            },
        )
        self.assertEqual(
            properties["VersioningConfiguration"]["Status"],
            "Enabled",
        )
        self.assertEqual(
            properties["BucketEncryption"][
                "ServerSideEncryptionConfiguration"
            ][0]["ServerSideEncryptionByDefault"]["SSEAlgorithm"],
            "AES256",
        )
        self.assertEqual(
            properties["OwnershipControls"]["Rules"],
            [{"ObjectOwnership": "BucketOwnerEnforced"}],
        )
        self.assertNotIn("AccessControl", properties)

    def test_public_access_block_is_complete(self) -> None:
        bucket = load_template()["Resources"]["SnapshotBucket"]  # type: ignore[index]
        block = bucket["Properties"]["PublicAccessBlockConfiguration"]
        self.assertEqual(
            block,
            {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        )

    def test_bucket_policy_has_only_explicit_denies(self) -> None:
        resources = load_template()["Resources"]
        policy = resources["SnapshotBucketPolicy"]  # type: ignore[index]
        statements = policy["Properties"]["PolicyDocument"]["Statement"]
        self.assertTrue(statements)
        self.assertTrue(
            all(statement["Effect"] == "Deny" for statement in statements)
        )
        self.assertFalse(
            any(statement["Effect"] == "Allow" for statement in statements)
        )
        by_sid = {statement["Sid"]: statement for statement in statements}
        self.assertEqual(
            by_sid["DenyNonTlsRequests"]["Condition"],
            {"Bool": {"aws:SecureTransport": "false"}},
        )
        self.assertEqual(
            by_sid["DenyGovernanceRetentionBypass"]["Action"],
            "s3:BypassGovernanceRetention",
        )

    def test_no_delete_or_bypass_permission_is_granted(self) -> None:
        resources = load_template()["Resources"]
        policy = resources["SnapshotBucketPolicy"]  # type: ignore[index]
        statements = policy["Properties"]["PolicyDocument"]["Statement"]
        allowed_actions = {
            action
            for statement in statements
            if statement["Effect"] == "Allow"
            for action in (
                statement["Action"]
                if isinstance(statement["Action"], list)
                else [statement["Action"]]
            )
        }
        self.assertEqual(allowed_actions, set())
        for forbidden in (
            "s3:DeleteObject",
            "s3:DeleteObjectVersion",
            "s3:DeleteBucket",
            "s3:BypassGovernanceRetention",
            "s3:*",
            "iam:*",
            "*",
        ):
            self.assertNotIn(forbidden, allowed_actions)

    def test_resource_retention_is_explicit_and_no_lifecycle_cleanup_exists(
        self,
    ) -> None:
        resources = load_template()["Resources"]
        for resource in resources.values():  # type: ignore[union-attr]
            self.assertEqual(resource["DeletionPolicy"], "Retain")
            self.assertEqual(resource["UpdateReplacePolicy"], "Retain")
        bucket = resources["SnapshotBucket"]  # type: ignore[index]
        self.assertNotIn("LifecycleConfiguration", bucket["Properties"])

    def test_outputs_are_non_secret_adapter_configuration_only(self) -> None:
        outputs = load_template()["Outputs"]
        self.assertEqual(
            set(outputs),
            {
                "BucketName",
                "BucketRegion",
                "DeploymentIdentifier",
                "ObjectKeyPrefix",
                "ObjectLockMode",
                "RetentionDays",
            },
        )
        rendered = json.dumps(outputs, sort_keys=True).casefold()
        for forbidden in (
            "credential",
            "accesskey",
            "secret",
            "sessiontoken",
            "calleridentity",
            "accountid",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertIsNone(re.search(r"\b\d{12}\b", rendered))

    def test_no_hardcoded_account_or_region_and_no_bootstrap_resource(
        self,
    ) -> None:
        rendered = TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b\d{12}\b", rendered))
        self.assertNotIn("CDKToolkit", rendered)
        self.assertNotIn("BootstrapVersion", rendered)
        self.assertNotIn("eu-central-1", rendered)

    def test_parameter_bounds_match_hackathon_retention_posture(self) -> None:
        parameters = load_template()["Parameters"]
        self.assertEqual(
            parameters["RetentionDays"],
            {
                "Default": 7,
                "Description": (
                    "Bounded development and hackathon retention period"
                ),
                "MaxValue": 30,
                "MinValue": 1,
                "Type": "Number",
            },
        )
        self.assertNotIn("Default", parameters["BucketName"])


if __name__ == "__main__":
    unittest.main()
