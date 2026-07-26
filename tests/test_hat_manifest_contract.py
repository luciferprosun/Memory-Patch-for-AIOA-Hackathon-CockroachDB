from __future__ import annotations

import json
import unittest
from dataclasses import fields, replace
from pathlib import Path

from tests._support import REPOSITORY_ROOT, make_manifest, make_scope
from aioa_memory_kernel.contracts import (
    AuthorityViolation,
    ContractValidationError,
    HatAuthorityDeclaration,
    HatManifest,
    HatScopeDimensionDefinition,
    HatSecurityPolicy,
    MissingDimensionBehavior,
    ScopeComparisonMode,
    ScopeValueType,
    assert_system_installed_hat,
    validate_hat_manifest,
    validate_scope_dimensions,
)


class HatManifestContractTests(unittest.TestCase):
    def test_valid_manifest_passes(self) -> None:
        validate_hat_manifest(make_manifest())

    def test_unsupported_manifest_schema_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError, "unsupported HAT manifest"
        ):
            replace(make_manifest(), schema_version="2.0.0")

    def test_hat_manifest_cannot_declare_external_action_authority(self) -> None:
        with self.assertRaises(AuthorityViolation):
            HatSecurityPolicy(external_action_authority="EXECUTE")  # type: ignore[arg-type]

    def test_hat_manifest_cannot_declare_canonical_write_authority(self) -> None:
        with self.assertRaises(AuthorityViolation):
            HatSecurityPolicy(canonical_write_authority="WRITE")  # type: ignore[arg-type]

    def test_hat_manifest_cannot_declare_approval_authority(self) -> None:
        with self.assertRaises(AuthorityViolation):
            HatSecurityPolicy(patch_approval_authority="APPROVE")  # type: ignore[arg-type]

    def test_hat_manifest_cannot_declare_commit_authority(self) -> None:
        with self.assertRaises(AuthorityViolation):
            HatSecurityPolicy(patch_commit_authority="COMMIT")  # type: ignore[arg-type]

    def test_authority_enum_has_only_none(self) -> None:
        self.assertEqual(
            tuple(HatAuthorityDeclaration), (HatAuthorityDeclaration.NONE,)
        )

    def test_two_unrelated_hat_fixtures_use_same_schema(self) -> None:
        fixture_path = (
            REPOSITORY_ROOT
            / "tests"
            / "fixtures"
            / "synthetic_contract_fixtures.json"
        )
        fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
        manifests = fixtures["hat_manifests"]
        self.assertEqual(len(manifests), 2)
        self.assertNotEqual(manifests[0]["domain_ids"], manifests[1]["domain_ids"])
        for raw in manifests:
            definitions = tuple(
                HatScopeDimensionDefinition(
                    name=item["name"],
                    value_type=ScopeValueType(item["value_type"]),
                    comparison_mode=ScopeComparisonMode(item["comparison_mode"]),
                    required=item["required"],
                    default_behavior=MissingDimensionBehavior(
                        item["default_behavior"]
                    ),
                    missing_creates_ambiguous=item[
                        "missing_creates_ambiguous"
                    ],
                    default_value=item.get("default_value"),
                    description=item.get("description", ""),
                )
                for item in raw["scope_dimension_definitions"]
            )
            manifest = HatManifest(
                schema_version=raw["schema_version"],
                hat_id=raw["hat_id"],
                hat_version=raw["hat_version"],
                display_name=raw["display_name"],
                domain_ids=tuple(raw["domain_ids"]),
                kernel_api_compatibility=raw["kernel_api_compatibility"],
                supported_languages=tuple(raw["supported_languages"]),
                scope_dimension_definitions=definitions,
                capabilities=tuple(raw["capabilities"]),
                source_authority_policy=raw["source_authority_policy"],
                retrieval_contract=raw["retrieval_contract"],
                claim_contract=raw["claim_contract"],
                conflict_contract=raw["conflict_contract"],
                memory_policy=raw["memory_policy"],
                security_policy=HatSecurityPolicy(),
                extension_points=raw["extension_points"],
            )
            validate_hat_manifest(manifest)

    def test_kernel_core_has_no_jurisdiction_requirement(self) -> None:
        field_names = {
            field.name
            for field in fields(HatManifest)
        } | {
            field.name
            for field in fields(HatScopeDimensionDefinition)
        }
        self.assertNotIn("jurisdiction", field_names)
        self.assertNotIn("legal_domain", field_names)

    def test_missing_declared_dimension_can_make_route_ambiguous(self) -> None:
        manifest = make_manifest()
        self.assertEqual(
            validate_scope_dimensions(
                manifest.scope_dimension_definitions, ()
            ),
            ("runtime_version",),
        )

    def test_unsupported_scope_dimension_is_rejected(self) -> None:
        manifest = make_manifest()
        with self.assertRaisesRegex(
            ContractValidationError, "unsupported scope dimensions"
        ):
            validate_scope_dimensions(
                manifest.scope_dimension_definitions,
                (make_scope(name="unregistered_dimension"),),
            )

    def test_duplicate_scope_definitions_are_rejected(self) -> None:
        definition = make_manifest().scope_dimension_definitions[0]
        with self.assertRaisesRegex(ContractValidationError, "unique names"):
            make_manifest().__class__(
                **{
                    **{
                        field.name: getattr(make_manifest(), field.name)
                        for field in fields(HatManifest)
                    },
                    "scope_dimension_definitions": (definition, definition),
                }
            )

    def test_manifest_cannot_enable_user_code(self) -> None:
        with self.assertRaises(AuthorityViolation):
            HatSecurityPolicy(executable_user_code=True)

    def test_manifest_cannot_enable_private_memory_access(self) -> None:
        with self.assertRaises(AuthorityViolation):
            HatSecurityPolicy(private_memory_access=True)

    def test_security_booleans_must_be_exact_false(self) -> None:
        with self.assertRaises(AuthorityViolation):
            HatSecurityPolicy(executable_user_code=0)  # type: ignore[arg-type]
        with self.assertRaises(AuthorityViolation):
            HatSecurityPolicy(private_memory_access=None)  # type: ignore[arg-type]

    def test_manifest_cannot_hide_action_authority_in_capabilities(self) -> None:
        with self.assertRaises(AuthorityViolation):
            replace(
                make_manifest(),
                capabilities=("REQUEST_NORMALIZATION", "SHELL_EXECUTION"),
            )

    def test_manifest_cannot_hide_authority_in_extension_points(self) -> None:
        with self.assertRaisesRegex(
            AuthorityViolation, "cannot redeclare HAT security key"
        ):
            replace(
                make_manifest(),
                extension_points={
                    "nested": {"external_action_authority": "EXECUTE"}
                },
            )

    def test_manifest_cannot_hide_private_access_in_policy(self) -> None:
        with self.assertRaisesRegex(
            AuthorityViolation, "cannot redeclare HAT security key"
        ):
            replace(
                make_manifest(),
                retrieval_contract={"private_memory_access": True},
            )

    def test_system_installed_sdk_shape_is_accepted_without_dynamic_loading(self) -> None:
        class SyntheticInstalledHat:
            manifest = make_manifest()

            def validate_manifest(self):
                return None

            def normalize_request(self, request):
                return request

            def derive_scope_requirements(self, request):
                return ()

            def build_retrieval_constraints(self, dimensions):
                return {}

            def rank_source_authority(self, source_metadata):
                return ()

            def extract_candidate_claims(self, draft_reference):
                return ()

            def detect_conflicts(self, evidence_references):
                return ()

            def create_correction_requirements(self, claim_references):
                return ()

            def create_memory_patch_proposal(self, correction_reference):
                return None

        assert_system_installed_hat(SyntheticInstalledHat())

    def test_incomplete_sdk_shape_is_rejected(self) -> None:
        class IncompleteHat:
            manifest = make_manifest()

        with self.assertRaises(ContractValidationError):
            assert_system_installed_hat(IncompleteHat())


if __name__ == "__main__":
    unittest.main()
