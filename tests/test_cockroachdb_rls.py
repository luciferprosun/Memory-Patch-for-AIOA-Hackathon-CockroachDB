"""Static and evidence-backed tests for Step 5 tenant/user SQL isolation."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SCRIPT = ROOT / "scripts" / "run_cockroachdb_migrations.py"
RLS_SCRIPT = ROOT / "scripts" / "run_cockroachdb_rls_validation.py"
MIGRATION_PATH = (
    ROOT
    / "sql"
    / "cockroachdb"
    / "migrations"
    / "0004_step5_tenant_roles_session_context_rls.sql"
)
SECURITY_MANIFEST_PATH = (
    ROOT / "config" / "cockroachdb" / "rls-security-1a.json"
)
SCHEMA_MANIFEST_PATH = (
    ROOT / "config" / "cockroachdb" / "schema-foundation-1a.json"
)
EVIDENCE_PATH = (
    ROOT
    / "docs"
    / "evidence"
    / "cockroachdb-v26-2"
    / "step5-rls-validation.json"
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


migrations = load_module("step5_migrations", MIGRATION_SCRIPT)
rls_validation = load_module("step5_rls_validation", RLS_SCRIPT)

STEP5_SQL = MIGRATION_PATH.read_text(encoding="utf-8")
SECURITY_MANIFEST = json.loads(SECURITY_MANIFEST_PATH.read_text(encoding="utf-8"))
SCHEMA_MANIFEST = json.loads(SCHEMA_MANIFEST_PATH.read_text(encoding="utf-8"))
EVIDENCE = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


class SecurityManifestCoverageTests(unittest.TestCase):
    def test_offline_security_validation_passes(self) -> None:
        result = rls_validation.offline_validate()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["migration_count"], 5)
        self.assertEqual(result["step4_table_count"], 29)
        self.assertEqual(result["protected_table_count"], 27)
        self.assertEqual(result["policy_count"], 50)
        self.assertEqual(result["identity_guard_trigger_count"], 2)

    def test_every_step4_table_is_classified_exactly_once(self) -> None:
        classified = [row["table"] for row in SECURITY_MANIFEST["tables"]]
        self.assertEqual(classified, SCHEMA_MANIFEST["required_tables"])
        self.assertEqual(len(classified), len(set(classified)))
        self.assertEqual(len(classified), 29)

    def test_access_classes_are_declared_and_nonempty(self) -> None:
        allowed = set(SECURITY_MANIFEST["access_classes"])
        self.assertTrue(allowed)
        for row in SECURITY_MANIFEST["tables"]:
            self.assertIn(row["access_class"], allowed)

    def test_every_tenant_scoped_table_has_exact_tenant_dimension(self) -> None:
        for row in SECURITY_MANIFEST["tables"]:
            if row["rls_enabled"]:
                self.assertEqual(row["tenant_column"], "tenant_id", row["table"])
                self.assertTrue(row["normal_runtime_access_required"], row["table"])

    def test_private_or_mixed_tables_have_a_user_dimension(self) -> None:
        private_classes = {
            "HAT_SCOPE_APPEND_ORIENTED",
            "HAT_SCOPE_MIXED",
            "TENANT_OR_USER_APPEND_ORIENTED",
            "USER_PRIVATE",
            "USER_PRIVATE_APPEND_ORIENTED",
        }
        for row in SECURITY_MANIFEST["tables"]:
            if row["access_class"] in private_classes:
                self.assertTrue(
                    row.get("user_owner_column")
                    or row.get("user_owner_resolution"),
                    row["table"],
                )

    def test_rls_exceptions_are_exact_and_justified(self) -> None:
        exceptions = {
            row["table"]: row["reason_for_rls_exception"]
            for row in SECURITY_MANIFEST["tables"]
            if not row["rls_enabled"]
        }
        self.assertEqual(set(exceptions), {"hat_manifests", "schema_migrations"})
        self.assertTrue(all(exceptions.values()))

    def test_security_context_table_is_internal_and_ungranted(self) -> None:
        self.assertEqual(
            SECURITY_MANIFEST["security_internal_tables"],
            [
                {
                    "access_class": "SECURITY_INTERNAL",
                    "append_model": "MUTABLE_TRANSACTION_CONTEXT",
                    "force_rls": False,
                    "normal_runtime_access_required": False,
                    "operations": {
                        "delete": "DENY_NO_GRANT",
                        "insert": "DENY_NO_GRANT",
                        "select": "DENY_NO_GRANT",
                        "update": "DENY_NO_GRANT",
                    },
                    "owner_role": "mp_security_owner",
                    "reason_for_rls_exception": (
                        "Only fully-qualified SECURITY DEFINER context functions "
                        "owned by the NOLOGIN security owner access this table; "
                        "PUBLIC and both ordinary roles have no table privilege."
                    ),
                    "rls_enabled": False,
                    "runtime_grants": [],
                    "table": "request_contexts",
                    "tenant_column": "tenant_id",
                    "user_owner_column": "user_id",
                }
            ],
        )

    def test_every_protected_table_has_rls_force_and_nonruntime_owner(self) -> None:
        protected = [row for row in SECURITY_MANIFEST["tables"] if row["rls_enabled"]]
        self.assertEqual(len(protected), 27)
        for row in protected:
            self.assertIs(row["force_rls"], True, row["table"])
            self.assertEqual(row["owner_role"], "mp_schema_owner", row["table"])
            self.assertNotEqual(row["owner_role"], "mp_app_runtime", row["table"])
            self.assertIsNone(row["reason_for_rls_exception"], row["table"])

    def test_operation_matrix_is_complete_and_matches_grants(self) -> None:
        grant_for_operation = {
            "delete": "DELETE",
            "insert": "INSERT",
            "select": "SELECT",
            "update": "UPDATE",
        }
        for row in SECURITY_MANIFEST["tables"]:
            self.assertEqual(
                set(row["operations"]),
                {"delete", "insert", "select", "update"},
                row["table"],
            )
            self.assertEqual(
                set(row["policies"]),
                {"delete", "insert", "select", "update"},
                row["table"],
            )
            for operation, grant in grant_for_operation.items():
                allowed = row["operations"][operation].startswith("ALLOW")
                self.assertEqual(
                    grant in row["runtime_grants"],
                    allowed,
                    (row["table"], operation),
                )
                self.assertEqual(
                    row["policies"][operation] is not None,
                    allowed and row["rls_enabled"],
                    (row["table"], operation),
                )

    def test_append_oriented_tables_have_no_update_or_delete_grant(self) -> None:
        for row in SECURITY_MANIFEST["tables"]:
            if row["append_model"] == "APPEND_ORIENTED":
                self.assertNotIn("UPDATE", row["runtime_grants"], row["table"])
                self.assertNotIn("DELETE", row["runtime_grants"], row["table"])


class MigrationSecurityDefinitionTests(unittest.TestCase):
    def test_role_set_is_minimal_and_fixed(self) -> None:
        self.assertEqual(
            {row["role"] for row in SECURITY_MANIFEST["roles"]},
            {
                "mp_app_runtime",
                "mp_request_context_setter",
                "mp_schema_owner",
                "mp_security_owner",
            },
        )
        self.assertEqual(STEP5_SQL.count("CREATE ROLE IF NOT EXISTS "), 4)

    def test_all_fixed_roles_are_nologin_and_nonprivileged(self) -> None:
        for role in SECURITY_MANIFEST["roles"]:
            self.assertIs(role["login"], False)
            self.assertIs(role["createrole"], False)
            self.assertIs(role["createdb"], False)
            self.assertIs(role["bypassrls"], False)
            self.assertEqual(role["member_of"], [])
            self.assertRegex(
                STEP5_SQL,
                rf"ALTER ROLE {role['role']}\s+"
                r"WITH NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;",
            )

    def test_no_model_hat_provider_critic_external_or_nvidia_role_exists(self) -> None:
        created_roles = re.findall(
            r"^CREATE ROLE IF NOT EXISTS ([a-z0-9_]+);",
            STEP5_SQL,
            re.MULTILINE,
        )
        for role in created_roles:
            self.assertNotRegex(
                role,
                r"(?i)(model|provider|critic|hat|external|nvidia|openshell|nooa)",
            )

    def test_runtime_never_owns_a_table_or_schema(self) -> None:
        self.assertNotRegex(
            STEP5_SQL,
            r"(?i)OWNER TO mp_app_runtime",
        )
        self.assertNotIn("GRANT CREATE ON SCHEMA memory_patch TO mp_app_runtime", STEP5_SQL)

    def test_public_and_runtime_privileges_are_explicitly_revoked(self) -> None:
        for fragment in (
            "REVOKE CREATE ON SCHEMA public FROM PUBLIC;",
            "REVOKE ALL ON SCHEMA memory_patch FROM PUBLIC;",
            "REVOKE ALL ON ALL TABLES IN SCHEMA memory_patch",
            "REVOKE ALL ON ALL SEQUENCES IN SCHEMA memory_patch",
            "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA memory_patch",
            "ALTER DEFAULT PRIVILEGES FOR ALL ROLES IN SCHEMA memory_patch",
        ):
            self.assertIn(fragment, STEP5_SQL)

    def test_runtime_has_no_security_or_migration_table_grant(self) -> None:
        self.assertRegex(
            STEP5_SQL,
            r"REVOKE ALL ON TABLE\s+"
            r"memory_patch\.schema_migrations,\s+"
            r"memory_patch\.request_contexts\s+"
            r"FROM PUBLIC, mp_app_runtime, mp_request_context_setter;",
        )

    def test_context_setter_execution_is_not_public(self) -> None:
        self.assertIn(
            "GRANT EXECUTE ON FUNCTION\n"
            "  memory_patch.set_request_context(STRING, STRING, STRING),\n"
            "  memory_patch.clear_request_context()\n"
            "TO mp_request_context_setter;",
            STEP5_SQL,
        )
        self.assertNotRegex(
            STEP5_SQL,
            r"(?is)GRANT EXECUTE ON FUNCTION.+?TO PUBLIC;",
        )

    def test_rls_and_force_coverage_matches_manifest(self) -> None:
        enabled = set(
            re.findall(
                r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
                r"ENABLE ROW LEVEL SECURITY;",
                STEP5_SQL,
            )
        )
        forced = set(
            re.findall(
                r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
                r"FORCE ROW LEVEL SECURITY;",
                STEP5_SQL,
            )
        )
        expected = {
            row["table"]
            for row in SECURITY_MANIFEST["tables"]
            if row["rls_enabled"]
        }
        self.assertEqual(enabled, expected)
        self.assertEqual(forced, expected)

    def test_policy_names_and_commands_match_manifest(self) -> None:
        expected = {
            policy: operation.upper()
            for row in SECURITY_MANIFEST["tables"]
            for operation, policy in row["policies"].items()
            if policy is not None
        }
        actual = {
            name: command.upper()
            for name, command in re.findall(
                r"CREATE POLICY IF NOT EXISTS ([a-z0-9_]+)\s+"
                r"ON memory_patch\.[a-z0-9_]+\s+"
                r"FOR (SELECT|INSERT|UPDATE|DELETE)",
                STEP5_SQL,
            )
        }
        self.assertEqual(len(actual), 50)
        self.assertEqual(actual, expected)

    def test_every_policy_targets_runtime_not_public(self) -> None:
        policy_blocks = re.findall(
            r"CREATE POLICY IF NOT EXISTS [\s\S]+?"
            r"(?=\nCREATE POLICY|\n-- STEP5_DATABASE_PHASE_[789]_END)",
            STEP5_SQL,
        )
        self.assertEqual(len(policy_blocks), 50)
        for block in policy_blocks:
            self.assertIn("TO mp_app_runtime", block)
            self.assertNotRegex(block, r"(?i)\bTO PUBLIC\b")

    def test_no_allow_all_or_context_fallback_policy_exists(self) -> None:
        self.assertNotRegex(STEP5_SQL, r"(?i)(USING|WITH CHECK)\s*\(\s*true\s*\)")
        self.assertNotRegex(STEP5_SQL, r"(?i)\bCOALESCE\s*\(")
        self.assertNotRegex(STEP5_SQL, r"(?i)DEFAULT_(TENANT|USER)")

    def test_command_specific_policy_shapes_are_present(self) -> None:
        for policy_name in (
            "personal_memory_spaces_s5_insert",
            "personal_memory_spaces_s5_update",
            "personal_memory_model_bindings_s5_delete",
        ):
            self.assertIn(policy_name, STEP5_SQL)
        update_blocks = re.findall(
            r"CREATE POLICY IF NOT EXISTS [a-z0-9_]+\s+"
            r"ON memory_patch\.[a-z0-9_]+\s+FOR UPDATE[\s\S]+?"
            r"(?=\nCREATE POLICY|\n-- STEP5_DATABASE_PHASE_4_END)",
            STEP5_SQL,
        )
        self.assertEqual(len(update_blocks), 2)
        for block in update_blocks:
            self.assertIn("USING", block)
            self.assertIn("WITH CHECK", block)

    def test_scope_identity_triggers_are_exact_and_non_authoritative(self) -> None:
        self.assertEqual(
            {
                (guard["table"], guard["trigger"], guard["function"])
                for guard in SECURITY_MANIFEST["scope_identity_guards"]
            },
            {
                (
                    "memory_items",
                    "memory_items_s5_identity_guard",
                    "guard_memory_item_identity",
                ),
                (
                    "personal_memory_spaces",
                    "personal_memory_spaces_s5_identity_guard",
                    "guard_personal_memory_space_identity",
                ),
            },
        )
        self.assertEqual(STEP5_SQL.count("\nCREATE TRIGGER "), 2)
        trigger_functions = STEP5_SQL[
            STEP5_SQL.index(
                "CREATE OR REPLACE FUNCTION memory_patch.guard_personal_memory_space_identity"
            ) : STEP5_SQL.index("-- STEP5_DATABASE_PHASE_1_END")
        ]
        self.assertNotRegex(
            trigger_functions,
            r"(?i)\b(APPROVE|COMMIT|ACTIVATE|PUBLISH|EXECUTE EXTERNAL)\b",
        )

    def test_context_is_transaction_bound_and_stable(self) -> None:
        for fragment in (
            "database_principal = session_user",
            "backend_pid = pg_catalog.pg_backend_pid()",
            "transaction_started_at",
            "= pg_catalog.transaction_timestamp()",
            "LANGUAGE SQL\nSTABLE\nSECURITY DEFINER",
        ):
            self.assertIn(fragment, STEP5_SQL)

    def test_context_setter_validates_caller_mode_tenant_and_user(self) -> None:
        for fragment in (
            "pg_catalog.pg_has_role(",
            "'mp_request_context_setter'",
            "'MEMBER'",
            "ERRCODE = '42501'",
            "ERRCODE = '22023'",
            "request_contexts_tenant_fk",
            "request_contexts_user_fk",
            "access_mode = 'TENANT_SHARED'",
            "access_mode = 'USER_PRIVATE'",
        ):
            self.assertIn(fragment, STEP5_SQL)

    def test_untrusted_custom_session_variables_are_not_policy_input(self) -> None:
        self.assertNotRegex(STEP5_SQL, r"(?i)\bcurrent_setting\s*\(")
        self.assertNotRegex(STEP5_SQL, r"(?i)\bSET\s+memory_patch\.")

    def test_step4_migration_hashes_remain_immutable(self) -> None:
        for migration_id, expected in migrations.STEP4_MIGRATION_HASHES.items():
            path = (
                ROOT
                / "sql"
                / "cockroachdb"
                / "migrations"
                / f"{migration_id}.sql"
            )
            self.assertEqual(migrations.file_sha256(path), expected)

    def test_step5_contains_no_secret_path_or_domain_rule(self) -> None:
        self.assertIsNone(migrations.SECRET_PATTERN.search(STEP5_SQL))
        self.assertNotRegex(STEP5_SQL, r"(?:/home/|/Users/|[A-Za-z]:\\\\)")
        self.assertNotRegex(
            STEP5_SQL,
            r"(?i)\b(Nachweisgesetz|German\s+law|German-law|employment\s+law)\b",
        )

    def test_step5_migration_does_not_implement_retry_or_idempotency(self) -> None:
        self.assertNotRegex(
            STEP5_SQL,
            r"(?i)\b(40001|retry loop|idempotency key|persistence adapter)\b",
        )


class MigrationApplicationSafetyTests(unittest.TestCase):
    def test_step5_is_split_into_cluster_ddl_and_nine_database_phases(self) -> None:
        cluster_sql, database_sql = migrations.split_step5_cluster_role_ddl(STEP5_SQL)
        self.assertEqual(cluster_sql.count("CREATE ROLE IF NOT EXISTS "), 4)
        self.assertNotRegex(database_sql, r"(?i)\bCREATE ROLE\b")
        self.assertEqual(len(migrations.split_step5_database_phases(database_sql)), 9)

    def test_step5_record_is_written_only_after_phases_and_catalog_check(self) -> None:
        migration = next(
            item
            for item in migrations.load_migrations()
            if item.migration_id == migrations.STEP5_MIGRATION_ID
        )
        states = [
            {},
            {migration.migration_id: migration.sha256},
        ]
        client = mock.Mock()
        with (
            mock.patch.object(migrations, "load_migrations", return_value=[migration]),
            mock.patch.object(migrations, "applied_migrations", side_effect=states),
            mock.patch.object(
                migrations,
                "assert_step5_security_catalog",
                return_value={"status": "PASS"},
            ) as catalog_check,
        ):
            result = migrations.apply_migrations(client, "mp_step5_unit_apply")
        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(client.execute.call_count, 11)
        catalog_check.assert_called_once()
        statements = [call.args[1] for call in client.execute.call_args_list]
        self.assertIn("CREATE ROLE IF NOT EXISTS", statements[0])
        self.assertNotIn("BEGIN;", statements[0])
        for statement in statements[1:10]:
            self.assertTrue(statement.startswith("BEGIN;\n"))
            self.assertTrue(statement.endswith("\nCOMMIT;"))
            self.assertNotIn(
                "INSERT INTO memory_patch.schema_migrations",
                statement,
            )
        self.assertIn("INSERT INTO memory_patch.schema_migrations", statements[-1])

    def test_failed_database_phase_is_not_recorded(self) -> None:
        migration = next(
            item
            for item in migrations.load_migrations()
            if item.migration_id == migrations.STEP5_MIGRATION_ID
        )
        client = mock.Mock()
        client.execute.side_effect = [
            "",
            migrations.SqlError("synthetic phase failure", sqlstate="42601"),
        ]
        with (
            mock.patch.object(migrations, "load_migrations", return_value=[migration]),
            mock.patch.object(migrations, "applied_migrations", return_value={}),
        ):
            with self.assertRaises(migrations.SqlError):
                migrations.apply_migrations(client, "mp_step5_unit_failure")
        statements = [call.args[1] for call in client.execute.call_args_list]
        self.assertFalse(
            any("INSERT INTO memory_patch.schema_migrations" in sql for sql in statements)
        )

    def test_failed_catalog_check_is_not_recorded(self) -> None:
        migration = next(
            item
            for item in migrations.load_migrations()
            if item.migration_id == migrations.STEP5_MIGRATION_ID
        )
        client = mock.Mock()
        client.execute.return_value = ""
        with (
            mock.patch.object(migrations, "load_migrations", return_value=[migration]),
            mock.patch.object(migrations, "applied_migrations", return_value={}),
            mock.patch.object(
                migrations,
                "assert_step5_security_catalog",
                side_effect=migrations.MigrationError("synthetic catalog mismatch"),
            ),
        ):
            with self.assertRaises(migrations.MigrationError):
                migrations.apply_migrations(client, "mp_step5_catalog_failure")
        statements = [call.args[1] for call in client.execute.call_args_list]
        self.assertFalse(
            any("INSERT INTO memory_patch.schema_migrations" in sql for sql in statements)
        )

    def test_live_rls_harness_requires_explicit_permission(self) -> None:
        with mock.patch.object(sys, "argv", [str(RLS_SCRIPT), "--live-test"]):
            self.assertEqual(rls_validation.main(), 1)

    def test_harness_has_no_broad_kill_or_shell_execution(self) -> None:
        source = RLS_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("pkill", source)
        self.assertNotIn("killall", source)
        self.assertNotIn("shell=True", source)
        self.assertIn(
            "migrations.drop_database(root, database, timeout=180)",
            source,
        )


class LiveRlsEvidenceTests(unittest.TestCase):
    def test_evidence_is_canonical_json_and_pinned(self) -> None:
        self.assertEqual(
            EVIDENCE_PATH.read_bytes(),
            migrations.canonical_json_bytes(EVIDENCE),
        )
        self.assertEqual(EVIDENCE["status"], "PASS")
        self.assertEqual(EVIDENCE["binary_identity"]["build_tag"], "v26.2.4")
        self.assertEqual(
            EVIDENCE["binary_identity"]["build_commit"],
            "80586181eb50e380e2cc982f61841eaf38af9982",
        )
        self.assertEqual(
            EVIDENCE["binary_identity"]["binary_sha256"],
            "a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf",
        )
        self.assertEqual(EVIDENCE["cluster_version"], "26.2")

    def test_evidence_proves_apply_noop_and_reproduction(self) -> None:
        migration = EVIDENCE["migration"]
        self.assertEqual(migration["first_apply"]["applied_count"], 4)
        self.assertEqual(migration["no_op_apply"]["applied_count"], 0)
        self.assertEqual(migration["no_op_apply"]["skipped_count"], 4)
        self.assertEqual(migration["reproduction_apply"]["applied_count"], 4)
        self.assertRegex(
            EVIDENCE["security_catalog"]["reproduction_digest"],
            r"^[0-9a-f]{64}$",
        )

    def test_evidence_catalog_matches_security_manifest(self) -> None:
        summary = EVIDENCE["security_catalog"]["summary"]
        self.assertEqual(summary["fixed_role_count"], 4)
        self.assertEqual(summary["protected_table_count"], 27)
        self.assertEqual(summary["policy_count"], 50)
        self.assertEqual(summary["runtime_table_grant_count"], 51)
        self.assertEqual(summary["identity_guard_trigger_count"], 2)
        self.assertEqual(len(EVIDENCE["security_catalog"]["policy_coverage"]), 50)
        self.assertEqual(len(EVIDENCE["security_catalog"]["table_security"]), 30)

    def test_live_fixed_roles_have_no_bypass_or_privileged_options(self) -> None:
        self.assertEqual(
            EVIDENCE["security_catalog"]["membership_edges_before_test_roles"],
            [],
        )
        for role in EVIDENCE["security_catalog"]["fixed_roles"]:
            for field in (
                "rolbypassrls",
                "rolcanlogin",
                "rolcreatedb",
                "rolcreaterole",
                "rolsuper",
            ):
                self.assertEqual(role[field], "f", (role["rolname"], field))

    def test_live_protected_tables_have_rls_force_and_separate_owner(self) -> None:
        protected = {
            row["table"]
            for row in SECURITY_MANIFEST["tables"]
            if row["rls_enabled"]
        }
        seen: set[str] = set()
        for row in EVIDENCE["security_catalog"]["table_security"]:
            if row["table_name"] in protected:
                seen.add(row["table_name"])
                self.assertEqual(row["relrowsecurity"], "t")
                self.assertEqual(row["relforcerowsecurity"], "t")
                self.assertEqual(row["owner_role"], "mp_schema_owner")
                self.assertNotEqual(row["owner_role"], "mp_app_runtime")
        self.assertEqual(seen, protected)

    def test_all_live_policy_rows_target_runtime(self) -> None:
        for policy in EVIDENCE["security_catalog"]["policy_coverage"]:
            self.assertIn("mp_app_runtime", policy["roles"])
            self.assertNotIn("public", policy["roles"].lower())

    def test_every_protected_table_was_seeded_and_failed_closed(self) -> None:
        coverage = EVIDENCE["security_catalog"]["table_coverage"]
        self.assertEqual(coverage["protected_table_count"], 27)
        self.assertEqual(coverage["root_nonempty_table_count"], 27)
        self.assertEqual(coverage["unset_context_zero_table_count"], 27)

    def test_all_95_probes_pass_by_required_category(self) -> None:
        summary = EVIDENCE["probe_summary"]
        self.assertEqual(summary["probe_count"], 95)
        self.assertEqual(summary["pass_count"], 95)
        self.assertEqual(summary["fail_count"], 0)
        self.assertEqual(
            summary["category_counts"],
            {
                "cross_tenant": 16,
                "cross_user": 17,
                "force_owner": 7,
                "migration": 1,
                "positive": 12,
                "role_escalation": 21,
                "session_context": 20,
                "table_coverage": 1,
            },
        )
        self.assertTrue(all(probe["status"] == "PASS" for probe in EVIDENCE["probes"]))

    def test_required_denial_sqlstates_are_actual_and_expected(self) -> None:
        probes = {probe["probe_id"]: probe for probe in EVIDENCE["probes"]}
        expected = {
            "TENANT-003": "42501",
            "TENANT-005": "23503",
            "TENANT-005A": "23503",
            "TENANT-005B": "23503",
            "TENANT-010A": "42501",
            "USER-006": "42501",
            "CTX-002": "22023",
            "CTX-004": "23503",
            "CTX-017": "42501",
            "FORCE-004": "42501",
            "ESC-001": "42501",
            "ESC-012": "01006",
        }
        for probe_id, sqlstate in expected.items():
            self.assertEqual(probes[probe_id]["sqlstate"], sqlstate)

    def test_positive_shared_and_private_paths_are_proven(self) -> None:
        probes = {probe["probe_id"]: probe for probe in EVIDENCE["probes"]}
        for probe_id in (
            "POS-001",
            "POS-002",
            "POS-003",
            "POS-004",
            "POS-005",
            "POS-006",
            "POS-007",
            "POS-008",
            "POS-009",
            "POS-010",
            "POS-011",
            "POS-012",
        ):
            self.assertEqual(probes[probe_id]["status"], "PASS")

    def test_cross_tenant_and_cross_user_categories_have_no_failure(self) -> None:
        for probe in EVIDENCE["probes"]:
            if probe["category"] in {"cross_tenant", "cross_user"}:
                self.assertEqual(probe["status"], "PASS")
        probes = {probe["probe_id"]: probe for probe in EVIDENCE["probes"]}
        self.assertEqual(probes["TENANT-006A"]["status"], "PASS")

    def test_context_reset_spoof_and_null_behavior_are_proven(self) -> None:
        probes = {probe["probe_id"]: probe for probe in EVIDENCE["probes"]}
        for probe_id in (
            "CTX-010",
            "CTX-011",
            "CTX-012",
            "CTX-013",
            "CTX-014",
            "CTX-015",
            "CTX-016",
            "CTX-017",
            "CTX-018",
            "CTX-019",
            "CTX-020",
        ):
            self.assertEqual(probes[probe_id]["status"], "PASS")

    def test_force_owner_and_role_escalation_are_proven(self) -> None:
        for probe in EVIDENCE["probes"]:
            if probe["category"] in {"force_owner", "role_escalation"}:
                self.assertEqual(probe["status"], "PASS")
        self.assertEqual(
            EVIDENCE["authority_and_role_escalation"]["runtime_only_memberships"],
            ["mp_app_runtime"],
        )

    def test_trust_boundary_is_stated_honestly(self) -> None:
        trust = EVIDENCE["trust_boundary"]
        self.assertIs(trust["transaction_bound"], True)
        self.assertIs(trust["database_principal_binding"], False)
        self.assertIs(trust["end_user_authentication_implemented"], False)
        self.assertEqual(trust["setter_role"], "mp_request_context_setter")

    def test_local_insecure_transport_limit_is_explicit(self) -> None:
        transport = EVIDENCE["insecure_local_transport"]
        self.assertIs(transport["used"], True)
        self.assertIs(transport["loopback_only"], True)
        self.assertIn("does not validate production certificates", transport["limitations"])

    def test_live_cleanup_is_complete_and_graceful(self) -> None:
        cleanup = EVIDENCE["cleanup"]
        for field in (
            "databases_removed",
            "disposable_and_fixed_roles_removed",
            "owned_pid_exited",
            "ports_closed",
            "temporary_store_removed",
        ):
            self.assertIs(cleanup[field], True)
        self.assertIs(cleanup["force_kill_used"], False)

    def test_evidence_contains_no_connection_or_secret_material(self) -> None:
        forbidden_keys = {
            "dsn",
            "password",
            "sql_url",
            "port",
            "temporary_path",
            "temporary_store",
        }
        keys: set[str] = set()

        def collect(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    keys.add(str(key).lower())
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(EVIDENCE)
        self.assertTrue(forbidden_keys.isdisjoint(keys))
        serialized = json.dumps(EVIDENCE, sort_keys=True)
        self.assertNotRegex(
            serialized,
            r"(?i)(postgres(?:ql)?://|cockroachdb://|BEGIN .*PRIVATE KEY|sk-[A-Za-z0-9])",
        )


class Step5DocumentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.architecture_path = (
            ROOT
            / "docs"
            / "architecture"
            / "COCKROACHDB_TENANT_ROLES_SESSION_CONTEXT_AND_RLS_1A.md"
        )
        self.adr_path = (
            ROOT
            / "docs"
            / "adr"
            / "ADR-012-cockroachdb-tenant-roles-session-context-rls.md"
        )
        self.closure_path = (
            ROOT
            / "docs"
            / "audits"
            / "STEP_5_TENANT_RLS_CLOSURE_1A.md"
        )

    def test_step5_document_package_exists(self) -> None:
        for path in (
            self.architecture_path,
            self.adr_path,
            self.closure_path,
            EVIDENCE_PATH,
        ):
            self.assertTrue(path.is_file(), path)

    def test_architecture_classifies_every_table(self) -> None:
        architecture = self.architecture_path.read_text(encoding="utf-8")
        for table in SCHEMA_MANIFEST["required_tables"]:
            self.assertIn(f"`memory_patch.{table}`", architecture)
        self.assertIn("`memory_patch.request_contexts`", architecture)

    def test_documentation_states_authentication_and_root_boundaries(self) -> None:
        architecture = self.architecture_path.read_text(encoding="utf-8")
        normalized = " ".join(architecture.split())
        for statement in (
            "It does not authenticate a human",
            "root/admin is outside the normal application path",
            "Changefeed output is not claimed to be tenant-filtered by RLS",
            "Step 6 persistence adapters, idempotency, and transaction retry were not started",
            "Step 36 remains the dedicated production credential-hardening step",
        ):
            self.assertIn(statement, normalized)

    def test_documentation_preserves_framework_neutral_nvidia_boundary(self) -> None:
        architecture = self.architecture_path.read_text(encoding="utf-8")
        self.assertIn("Framework-neutral extension points are preserved", architecture)
        self.assertIn("no NVIDIA-branded", architecture)

    def test_documentation_index_links_complete_step5_package(self) -> None:
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        for relative in (
            "architecture/COCKROACHDB_TENANT_ROLES_SESSION_CONTEXT_AND_RLS_1A.md",
            "adr/ADR-012-cockroachdb-tenant-roles-session-context-rls.md",
            "evidence/cockroachdb-v26-2/step5-rls-validation.json",
            "audits/STEP_5_TENANT_RLS_CLOSURE_1A.md",
        ):
            self.assertIn(relative, index)

    def test_roadmap_preserves_step5_and_names_step7_next(self) -> None:
        roadmap = (
            ROOT / "docs" / "roadmap" / "PRODUCTION_ROADMAP.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "- [x] **Step 5 — Tenant Roles, Session Context and Row-Level Security 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 6 — Persistence Adapters, Idempotency and Transaction Retry Foundation 1A**",
            roadmap,
        )
        self.assertIn(
            "Dokładny następny krok: `Step 7 — S3 Snapshot Authority and Object Lock Adapter 1A`",
            roadmap,
        )
        self.assertIn(
            "- [ ] **Step 7 — S3 Snapshot Authority and Object Lock Adapter 1A**",
            roadmap,
        )


if __name__ == "__main__":
    unittest.main()
