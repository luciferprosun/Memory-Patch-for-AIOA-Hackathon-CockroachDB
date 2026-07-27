"""Offline safety and contract tests for the Step 4 migration foundation."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_cockroachdb_migrations.py"
SPEC = importlib.util.spec_from_file_location(
    "run_cockroachdb_migrations", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
migrations = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = migrations
SPEC.loader.exec_module(migrations)

SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aioa_memory_kernel.contracts import (  # noqa: E402
    ActionPolicy,
    ActorType,
    ApprovalDecision,
    EvidenceStatus,
    KnowledgeRoute,
    MemoryContentKind,
    MemoryTargetScope,
    MemoryTrustClass,
    PatchState,
    PersonalMemorySpaceState,
)


SQL_ROOT = ROOT / "sql" / "cockroachdb" / "migrations"
MIGRATION_SQL = "\n".join(
    path.read_text(encoding="utf-8") for path in sorted(SQL_ROOT.glob("*.sql"))
)


class OfflineManifestTests(unittest.TestCase):
    def test_offline_validation_passes(self) -> None:
        result = migrations.offline_validate()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["target_version"], "v26.2.4")
        self.assertEqual(result["migration_count"], 3)
        self.assertEqual(result["schema_table_count"], 29)

    def test_migration_order_and_ids_are_stable(self) -> None:
        loaded = migrations.load_migrations()
        ids = [item.migration_id for item in loaded]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(
            ids,
            [
                "0001_step4_identity_and_hat_scopes",
                "0002_step4_knowledge_lineage_and_retrieval",
                "0003_step4_kernel_memory_and_audit_evidence",
            ],
        )

    def test_migration_ids_and_filenames_are_one_to_one(self) -> None:
        loaded = migrations.load_migrations()
        self.assertEqual(
            [item.filename for item in loaded],
            [f"{item.migration_id}.sql" for item in loaded],
        )
        self.assertEqual(len({item.migration_id for item in loaded}), len(loaded))

    def test_migration_checksums_match_files(self) -> None:
        for item in migrations.load_migrations():
            self.assertEqual(migrations.file_sha256(item.path), item.sha256)

    def test_no_undeclared_migration_file_exists(self) -> None:
        declared = {item.filename for item in migrations.load_migrations()}
        discovered = {path.name for path in SQL_ROOT.glob("*.sql")}
        self.assertEqual(discovered, declared)

    def test_json_manifests_are_canonical(self) -> None:
        for path in (
            migrations.MIGRATION_MANIFEST_PATH,
            migrations.SCHEMA_MANIFEST_PATH,
        ):
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(path.read_bytes(), migrations.canonical_json_bytes(value))

    def test_version_pin_is_exact(self) -> None:
        pin = migrations.load_version_pin()
        self.assertEqual(pin["exact_version"], "v26.2.4")
        self.assertEqual(pin["runtime"]["cluster_version"], "26.2")

    def test_schema_manifest_matches_created_tables(self) -> None:
        expected = migrations.load_schema_manifest()["required_tables"]
        actual = sorted(
            re.findall(
                r"^CREATE TABLE memory_patch\.([a-z0-9_]+)",
                MIGRATION_SQL,
                re.MULTILINE,
            )
        )
        self.assertEqual(actual, expected)

    def test_required_entities_are_covered(self) -> None:
        required = {
            "tenants",
            "users",
            "hat_manifests",
            "hat_scopes",
            "personal_memory_spaces",
            "knowledge_sources",
            "source_snapshots",
            "knowledge_versions",
            "knowledge_chunks",
            "kernel_runs",
            "routing_decisions",
            "schema_migrations",
        }
        tables = set(migrations.load_schema_manifest()["required_tables"])
        self.assertTrue(required.issubset(tables))

    def test_explicit_index_manifest_matches_sql(self) -> None:
        expected = migrations.load_schema_manifest()["explicit_indexes"]
        actual = sorted(
            re.findall(
                r"^CREATE (?:UNIQUE |INVERTED )?INDEX ([a-z0-9_]+)",
                MIGRATION_SQL,
                re.MULTILINE,
            )
        )
        self.assertEqual(actual, expected)


class CanonicalContractVocabularyTests(unittest.TestCase):
    def test_personal_memory_states_are_canonical(self) -> None:
        for state in PersonalMemorySpaceState:
            self.assertIn(f"'{state.value}'", MIGRATION_SQL)

    def test_patch_states_are_canonical(self) -> None:
        transition_sql = (SQL_ROOT / "0003_step4_kernel_memory_and_audit_evidence.sql").read_text(
            encoding="utf-8"
        )
        for state in PatchState:
            self.assertIn(f"'{state.value}'", transition_sql)

    def test_route_and_policy_values_are_canonical(self) -> None:
        for value in (*KnowledgeRoute, *ActionPolicy, *EvidenceStatus):
            self.assertIn(f"'{value.value}'", MIGRATION_SQL)

    def test_memory_contract_values_are_canonical(self) -> None:
        for value in (
            *MemoryTargetScope,
            *MemoryTrustClass,
            *MemoryContentKind,
        ):
            if value in {
                MemoryTargetScope.SESSION,
                MemoryTrustClass.SESSION_MEMORY,
            }:
                continue
            self.assertIn(value.value, MIGRATION_SQL)

    def test_approval_and_actor_values_are_canonical(self) -> None:
        for decision in ApprovalDecision:
            self.assertIn(f"'{decision.value}'", MIGRATION_SQL)
        for actor in (
            ActorType.USER,
            ActorType.HUMAN_REVIEWER,
            ActorType.COMMIT_SERVICE,
            ActorType.MIGRATION_SERVICE,
        ):
            self.assertIn(f"'{actor.value}'", MIGRATION_SQL)


class TenantLineageAndAuthorityStaticTests(unittest.TestCase):
    def test_hat_scope_eliminates_nullable_ownership_ambiguity(self) -> None:
        self.assertIn("hat_scopes_exact_ownership", MIGRATION_SQL)
        self.assertRegex(
            MIGRATION_SQL,
            r"target_scope = 'SHARED_KNOWLEDGE_HAT'[\s\S]+?"
            r"owner_user_id IS NULL[\s\S]+?personal_memory_space_id IS NULL",
        )
        self.assertRegex(
            MIGRATION_SQL,
            r"target_scope = 'USER_PERSONAL_HAT'[\s\S]+?"
            r"owner_user_id IS NOT NULL[\s\S]+?"
            r"personal_memory_space_id IS NOT NULL",
        )

    def test_personal_space_has_exact_composite_owner_key(self) -> None:
        self.assertIn(
            "PRIMARY KEY (tenant_id, user_id, personal_memory_space_id)",
            MIGRATION_SQL,
        )
        self.assertIn("personal_memory_spaces_user_fk", MIGRATION_SQL)

    def test_lineage_has_composite_tenant_and_scope_foreign_keys(self) -> None:
        for constraint in (
            "source_snapshots_source_fk",
            "knowledge_versions_snapshot_fk",
            "knowledge_chunks_version_fk",
            "chunk_search_documents_chunk_fk",
        ):
            self.assertIn(constraint, MIGRATION_SQL)

    def test_lineage_delete_behavior_is_restrictive(self) -> None:
        self.assertNotRegex(MIGRATION_SQL, r"(?i)ON\s+DELETE\s+CASCADE")
        self.assertGreaterEqual(
            len(re.findall(r"ON DELETE RESTRICT", MIGRATION_SQL)),
            20,
        )

    def test_no_step5_security_ddl_is_present(self) -> None:
        for pattern in (
            r"(?i)\bCREATE\s+ROLE\b",
            r"(?i)\bCREATE\s+POLICY\b",
            r"(?i)\bENABLE\s+ROW\s+LEVEL\s+SECURITY\b",
            r"(?i)\bFORCE\s+ROW\s+LEVEL\s+SECURITY\b",
            r"(?i)\bBYPASSRLS\b",
        ):
            self.assertNotRegex(MIGRATION_SQL, pattern)

    def test_no_database_trigger_or_generated_authority_exists(self) -> None:
        self.assertNotRegex(MIGRATION_SQL, r"(?i)\bCREATE\s+TRIGGER\b")
        self.assertNotRegex(
            MIGRATION_SQL,
            r"(?i)\bDEFAULT\s+'?(APPROVED|COMMITTED|ACTIVE|HUMAN|TRUSTED)'?",
        )

    def test_models_and_hats_cannot_claim_database_authority(self) -> None:
        self.assertIn(
            "CHECK (approver_type IN ('USER', 'HUMAN_REVIEWER'))",
            MIGRATION_SQL,
        )
        for authority in (
            "hat_manifests_no_approval_authority",
            "hat_manifests_no_commit_authority",
            "hat_manifests_no_canonical_write_authority",
            "hat_manifests_no_external_action_authority",
        ):
            self.assertIn(authority, MIGRATION_SQL)

    def test_routing_decisions_have_no_authority_columns(self) -> None:
        routing = MIGRATION_SQL.split(
            "CREATE TABLE memory_patch.routing_decisions", 1
        )[1].split("CREATE TABLE", 1)[0]
        for forbidden in ("approved", "committed", "can_write", "authority"):
            self.assertNotIn(forbidden, routing.lower())

    def test_verified_memory_is_inert_at_this_layer(self) -> None:
        self.assertIn("memory_items_verified_inert", MIGRATION_SQL)
        self.assertIn("active = false", MIGRATION_SQL)

    def test_commit_receipt_requires_approved_binding(self) -> None:
        self.assertIn("memory_patch_commits_approval_fk", MIGRATION_SQL)
        self.assertIn(
            "memory_patch_commits_personal_approval_fk",
            MIGRATION_SQL,
        )
        self.assertIn("CHECK (approval_decision = 'APPROVE')", MIGRATION_SQL)
        self.assertIn(
            "CHECK (actor_type IN ('COMMIT_SERVICE', 'MIGRATION_SERVICE'))",
            MIGRATION_SQL,
        )

    def test_personal_approval_and_commit_cannot_omit_owner_scope(self) -> None:
        self.assertIn("memory_patch_approvals_exact_scope", MIGRATION_SQL)
        self.assertIn("memory_patch_commits_exact_scope", MIGRATION_SQL)
        self.assertRegex(
            MIGRATION_SQL,
            r"memory_patch_approvals_exact_scope[\s\S]+?"
            r"target_scope = 'USER_PERSONAL_HAT'[\s\S]+?"
            r"owner_user_id IS NOT NULL",
        )
        self.assertRegex(
            MIGRATION_SQL,
            r"memory_patch_commits_exact_scope[\s\S]+?"
            r"target_scope = 'USER_PERSONAL_HAT'[\s\S]+?"
            r"owner_user_id IS NOT NULL",
        )

    def test_kernel_hat_evidence_requires_shared_hat_scope(self) -> None:
        for constraint in (
            "routing_decisions_selected_hat_fk",
            "evidence_bundles_scope_fk",
            "correction_packets_scope_fk",
        ):
            self.assertIn(constraint, MIGRATION_SQL)
        self.assertIn(
            "REFERENCES memory_patch.hat_scopes (\n"
            "      tenant_id,\n"
            "      hat_scope_id,\n"
            "      knowledge_hat_id\n"
            "    )",
            MIGRATION_SQL,
        )

    def test_static_transition_edges_match_contract_graph(self) -> None:
        self.assertIn("patch_transition_records_allowed_edge", MIGRATION_SQL)
        self.assertIn(
            "state_before = 'PROPOSED' AND state_after = 'EVIDENCE_BOUND'",
            MIGRATION_SQL,
        )
        self.assertNotIn(
            "state_before = 'PROPOSED' AND state_after = 'APPROVED'",
            MIGRATION_SQL,
        )

    def test_no_domain_specific_kernel_schema_rule_exists(self) -> None:
        self.assertNotRegex(
            MIGRATION_SQL,
            r"(?i)\b(Nachweisgesetz|German\s+law|German-law|employment\s+law)\b",
        )

    def test_no_secret_or_machine_path_exists(self) -> None:
        self.assertIsNone(migrations.SECRET_PATTERN.search(MIGRATION_SQL))
        self.assertNotRegex(MIGRATION_SQL, r"(?:/home/|/Users/|[A-Za-z]:\\\\)")


class RetrievalBoundaryTests(unittest.TestCase):
    def test_full_text_storage_is_language_explicit(self) -> None:
        self.assertIn("search_config STRING NOT NULL", MIGRATION_SQL)
        self.assertIn("search_vector TSVECTOR NOT NULL", MIGRATION_SQL)
        self.assertIn("chunk_search_documents_vector_idx", MIGRATION_SQL)
        self.assertIn("'english'", MIGRATION_SQL)
        self.assertIn("'german'", MIGRATION_SQL)
        self.assertIn("'simple'", MIGRATION_SQL)

    def test_vector_dimension_is_not_fabricated(self) -> None:
        self.assertNotRegex(MIGRATION_SQL, r"(?i)\bVECTOR\s*\(")
        manifest = migrations.load_schema_manifest()
        self.assertIn(
            "VECTOR_COLUMN_AND_INDEX_PENDING_MODEL_AND_DIMENSION_PIN",
            manifest["deferred_features"],
        )

    def test_prefix_indexes_are_filtering_not_authority(self) -> None:
        self.assertIn("knowledge_chunks_scope_retrieval_idx", MIGRATION_SQL)
        self.assertIn("tenant_id,\n    hat_scope_id", MIGRATION_SQL)
        self.assertNotIn("authorization", MIGRATION_SQL.lower())


class LiveEvidenceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = (
            ROOT
            / "docs"
            / "evidence"
            / "cockroachdb-v26-2"
            / "step4-schema-validation.json"
        )
        self.evidence = json.loads(self.path.read_text(encoding="utf-8"))

    def test_live_evidence_is_canonical_and_pinned(self) -> None:
        self.assertEqual(
            self.path.read_bytes(),
            migrations.canonical_json_bytes(self.evidence),
        )
        self.assertEqual(self.evidence["status"], "PASS")
        self.assertEqual(
            self.evidence["binary_identity"]["build_tag"],
            "v26.2.4",
        )
        self.assertEqual(self.evidence["cluster_version"], "26.2")

    def test_live_evidence_records_complete_migration_behavior(self) -> None:
        self.assertEqual(self.evidence["first_apply"]["applied_count"], 3)
        self.assertEqual(self.evidence["no_op_apply"]["applied_count"], 0)
        self.assertEqual(self.evidence["no_op_apply"]["skipped_count"], 3)
        self.assertEqual(self.evidence["reproduction_apply"]["applied_count"], 3)

    def test_live_evidence_records_catalog_and_reproduction(self) -> None:
        schema = self.evidence["schema"]
        self.assertEqual(schema["table_count"], 29)
        self.assertGreater(schema["column_count"], 0)
        self.assertEqual(schema["constraint_counts"]["FOREIGN KEY"], 44)
        self.assertRegex(schema["schema_digest"], r"^[0-9a-f]{64}$")

    def test_live_evidence_records_expected_negative_sqlstates(self) -> None:
        states = self.evidence["probes"]["negative_sqlstates"]
        self.assertEqual(states["cross_tenant_snapshot"], "23503")
        self.assertEqual(states["cross_hat_snapshot"], "23503")
        self.assertEqual(states["cross_tenant_version"], "23503")
        self.assertEqual(states["cross_tenant_chunk"], "23503")
        self.assertEqual(states["cross_hat_chunk"], "23503")
        self.assertEqual(states["cross_owner_scope"], "23503")
        self.assertEqual(states["duplicate_source_identity"], "23505")
        self.assertEqual(states["model_cannot_claim_approval"], "23514")
        self.assertEqual(states["hat_cannot_claim_authority"], "23514")
        self.assertEqual(states["verified_memory_stays_inert"], "23514")
        self.assertEqual(states["forbidden_transition_edge"], "23514")
        self.assertEqual(states["cross_owner_commit_binding"], "23503")
        self.assertEqual(states["personal_approval_requires_owner"], "23514")
        self.assertEqual(states["personal_approval_scope_is_exact"], "23503")
        self.assertEqual(states["personal_commit_requires_owner"], "23514")
        self.assertEqual(
            states["personal_scope_cannot_replace_knowledge_hat"],
            "23503",
        )
        self.assertEqual(states["private_scope_requires_owner"], "23514")
        self.assertEqual(states["shared_scope_rejects_private_owner"], "23514")

    def test_live_evidence_proves_cleanup(self) -> None:
        cleanup = self.evidence["cleanup"]
        for field in (
            "databases_removed",
            "no_credentials_created",
            "owned_pid_exited",
            "ports_closed",
            "temporary_store_removed",
        ):
            self.assertIs(cleanup[field], True)

    def test_live_evidence_contains_no_secret_shaped_value(self) -> None:
        serialized = json.dumps(self.evidence, sort_keys=True)
        self.assertIsNone(migrations.SECRET_PATTERN.search(serialized))
        keys: set[str] = set()

        def collect_keys(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    keys.add(str(key).lower())
                    collect_keys(child)
            elif isinstance(value, list):
                for child in value:
                    collect_keys(child)

        collect_keys(self.evidence)
        for forbidden_key in (
            "dsn",
            "password",
            "sql_url",
            "port",
            "temporary_path",
        ):
            self.assertNotIn(forbidden_key, keys)


class MigrationRunnerSafetyTests(unittest.TestCase):
    def test_live_actions_require_explicit_permission(self) -> None:
        with mock.patch.object(
            sys, "argv", [str(SCRIPT), "--live-test"]
        ):
            self.assertEqual(migrations.main(), 1)

    def test_database_identifier_is_strict(self) -> None:
        for invalid in ("Prod", "a", "db-name", "db;drop", " db", "db.name"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(migrations.MigrationError):
                    migrations.validate_database_identifier(invalid)

    def test_cleanup_requires_owned_database_marker(self) -> None:
        with self.assertRaises(migrations.MigrationError):
            migrations.assert_disposable_database("production_database")
        migrations.assert_disposable_database("mp_step4_safe_fixture")

    def test_runtime_path_requires_direct_tmp_ownership(self) -> None:
        with self.assertRaises(migrations.MigrationError):
            migrations.assert_owned_runtime_path(Path("/tmp/unowned"))
        with self.assertRaises(migrations.MigrationError):
            migrations.assert_owned_runtime_path(Path("/tmp/parent/mp_step4_nested"))

    def test_timeout_is_bounded(self) -> None:
        for invalid in (0, -1, 181, True, "60"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(migrations.MigrationError):
                    migrations.validate_timeout(invalid)  # type: ignore[arg-type]

    def test_insecure_runtime_is_loopback_only(self) -> None:
        migrations.require_loopback("127.0.0.1")
        with self.assertRaises(migrations.MigrationError):
            migrations.require_loopback("0.0.0.0")

    def test_binary_version_mismatch_is_rejected(self) -> None:
        fake_result = migrations.ProcessResult(
            0,
            "Build Tag: v26.2.3\n",
            "",
        )
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "cockroach"
            binary.write_bytes(b"synthetic")
            with mock.patch.object(
                migrations, "run_process", return_value=fake_result
            ):
                with self.assertRaisesRegex(
                    migrations.MigrationError, "version mismatch"
                ):
                    migrations.verify_binary_identity(binary)

    def test_apply_uses_one_transaction_and_records_after_sql(self) -> None:
        loaded = migrations.load_migrations()
        snapshots: list[dict[str, str]] = [{}]
        current: dict[str, str] = {}
        for item in loaded:
            current = {**current, item.migration_id: item.sha256}
            snapshots.append(copy.deepcopy(current))
        client = mock.Mock()
        with mock.patch.object(
            migrations,
            "applied_migrations",
            side_effect=snapshots,
        ):
            result = migrations.apply_migrations(client, "mp_step4_apply")
        self.assertEqual(result["applied_count"], 3)
        self.assertEqual(client.execute.call_count, 3)
        for call in client.execute.call_args_list:
            statement = call.args[1]
            self.assertTrue(statement.startswith("BEGIN;\n"))
            self.assertTrue(statement.endswith("\nCOMMIT;"))
            self.assertIn(
                "INSERT INTO memory_patch.schema_migrations",
                statement,
            )

    def test_second_apply_is_non_destructive_noop(self) -> None:
        loaded = migrations.load_migrations()
        existing = {item.migration_id: item.sha256 for item in loaded}
        client = mock.Mock()
        with mock.patch.object(
            migrations, "applied_migrations", return_value=existing
        ):
            result = migrations.apply_migrations(client, "mp_step4_noop")
        self.assertEqual(result["applied_count"], 0)
        self.assertEqual(result["skipped_count"], 3)
        client.execute.assert_not_called()

    def test_applied_checksum_mismatch_fails_closed(self) -> None:
        first = migrations.load_migrations()[0]
        client = mock.Mock()
        with mock.patch.object(
            migrations,
            "applied_migrations",
            return_value={first.migration_id: "0" * 64},
        ):
            with self.assertRaisesRegex(
                migrations.MigrationError, "checksum mismatch"
            ):
                migrations.apply_migrations(client, "mp_step4_mismatch")

    def test_failed_migration_is_not_reported_as_applied(self) -> None:
        client = mock.Mock()
        client.execute.side_effect = migrations.SqlError(
            "synthetic failure", sqlstate="42601"
        )
        with mock.patch.object(
            migrations, "applied_migrations", return_value={}
        ):
            with self.assertRaises(migrations.SqlError):
                migrations.apply_migrations(client, "mp_step4_failure")
        self.assertEqual(client.execute.call_count, 1)

    def test_runner_contains_no_broad_process_kill(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("pkill", source)
        self.assertNotIn("killall", source)
        self.assertNotIn("shell=True", source)


class DocumentationContractTests(unittest.TestCase):
    def test_step4_documents_exist_and_links_resolve(self) -> None:
        required = (
            ROOT
            / "docs"
            / "architecture"
            / "COCKROACHDB_LOGICAL_SCHEMA_AND_MIGRATION_FOUNDATION_1A.md",
            ROOT / "docs" / "adr" / "ADR-011-cockroachdb-schema-migrations.md",
            ROOT
            / "docs"
            / "audits"
            / "STEP_4_COCKROACHDB_SCHEMA_MIGRATION_CLOSURE_1A.md",
        )
        for path in required:
            self.assertTrue(path.is_file(), path)

    def test_documented_table_names_match_manifest(self) -> None:
        architecture = (
            ROOT
            / "docs"
            / "architecture"
            / "COCKROACHDB_LOGICAL_SCHEMA_AND_MIGRATION_FOUNDATION_1A.md"
        ).read_text(encoding="utf-8")
        for table in migrations.load_schema_manifest()["required_tables"]:
            self.assertIn(f"`memory_patch.{table}`", architecture)

    def test_roadmap_closes_step4_and_leaves_step5_open(self) -> None:
        roadmap = (
            ROOT / "docs" / "roadmap" / "PRODUCTION_ROADMAP.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "- [x] **Step 4 — CockroachDB Logical Schema and Migration Foundation 1A**",
            roadmap,
        )
        self.assertIn(
            "- [ ] **Step 5 — Tenant Roles, Session Context and Row-Level Security 1A**",
            roadmap,
        )
        self.assertIn(
            "Dokładny następny krok: `Step 5 — Tenant Roles, Session Context and Row-Level Security 1A`",
            roadmap,
        )

    def test_documentation_states_sql_isolation_boundary_exactly(self) -> None:
        architecture = (
            ROOT
            / "docs"
            / "architecture"
            / "COCKROACHDB_LOGICAL_SCHEMA_AND_MIGRATION_FOUNDATION_1A.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "The logical schema is tenant-ready. SQL-enforced tenant isolation "
            "is not complete until Step 5.",
            architecture,
        )

    def test_step4_links_are_local_and_resolve(self) -> None:
        documents = (
            ROOT / "docs" / "README.md",
            ROOT
            / "docs"
            / "architecture"
            / "COCKROACHDB_LOGICAL_SCHEMA_AND_MIGRATION_FOUNDATION_1A.md",
            ROOT / "docs" / "adr" / "ADR-011-cockroachdb-schema-migrations.md",
            ROOT
            / "docs"
            / "audits"
            / "STEP_4_COCKROACHDB_SCHEMA_MIGRATION_CLOSURE_1A.md",
            ROOT / "docs" / "roadmap" / "PRODUCTION_ROADMAP.md",
        )
        for document in documents:
            text = document.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                if (
                    "://" in target
                    or target.startswith("#")
                    or target.startswith("mailto:")
                ):
                    continue
                path_target = target.split("#", 1)[0]
                if not path_target:
                    continue
                resolved = (document.parent / path_target).resolve()
                self.assertTrue(
                    resolved.is_relative_to(ROOT.resolve()),
                    (document, target),
                )
                self.assertTrue(resolved.exists(), (document, target))

    def test_documentation_index_links_step4_package(self) -> None:
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        for relative in (
            "architecture/COCKROACHDB_LOGICAL_SCHEMA_AND_MIGRATION_FOUNDATION_1A.md",
            "adr/ADR-011-cockroachdb-schema-migrations.md",
            "evidence/cockroachdb-v26-2/step4-schema-validation.json",
            "audits/STEP_4_COCKROACHDB_SCHEMA_MIGRATION_CLOSURE_1A.md",
        ):
            self.assertIn(relative, index)


if __name__ == "__main__":
    unittest.main()
