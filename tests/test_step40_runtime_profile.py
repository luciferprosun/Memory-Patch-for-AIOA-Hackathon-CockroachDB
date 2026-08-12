from __future__ import annotations

import dataclasses
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256
from aioa_memory_kernel.runtime.resource_profile import (
    DEFAULT_PROFILE_PATH,
    PROFILE_ID,
    Runtime4GBProfile,
    load_runtime_4gb_profile,
    verify_runtime_4gb_profile,
)
from aioa_memory_kernel.security.redaction import assert_secret_free


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STEP40_EVIDENCE = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "performance"
    / "step40-4gb-resource-validation.json"
)
STEP40_EVIDENCE_FILE_SHA256 = (
    "17b58c819190f4d4e096ac446989ed9b9f6753d1ee4dbfc3dcf5e00682c6142f"
)
STEP40_VALIDATION_DIGEST = (
    "22d49082321b80ceb91eb94aaaf305fb0c20566b863f4803a69e2ff1b7ce5017"
)


class Step40RuntimeProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_runtime_4gb_profile()

    def test_profile_identity_digest_and_schema_are_exact(self) -> None:
        self.assertEqual(self.profile.profile_id, PROFILE_ID)
        self.assertEqual(
            self.profile.profile_digest,
            canonical_sha256(self.profile, exclude_fields=("profile_digest",)),
        )
        self.assertEqual(self.profile.schema_version, "1.0.0")
        self.assertEqual(self.profile.profile_version, "1.0.0")
        self.assertEqual(DEFAULT_PROFILE_PATH.name, "4gb-demo-1a.json")

    def test_profile_freezes_process_and_remote_dependency_layout(self) -> None:
        self.assertEqual(self.profile.process_layout.web_workers, 1)
        self.assertEqual(self.profile.process_layout.additional_frontend_processes, 0)
        self.assertEqual(self.profile.process_layout.local_generation_model_processes, 0)
        self.assertEqual(self.profile.process_layout.ingestion_workers, 0)
        self.assertEqual(self.profile.database.topology, "REMOTE_REQUIRED_SERVICE")
        self.assertEqual(self.profile.database.local_cockroach_processes, 0)
        self.assertFalse(self.profile.database.production_ha_proven)
        self.assertTrue(self.profile.database.single_node_demo_available)

    def test_profile_preserves_core_and_makes_only_optional_services_dormant(self) -> None:
        policy = self.profile.optional_services
        self.assertTrue(policy.audit_enabled)
        self.assertFalse(policy.critic_enabled_by_default)
        self.assertFalse(policy.ingestion_enabled_by_default)
        self.assertTrue(policy.ingestion_requires_prepared_corpus)
        self.assertTrue(policy.review_request_driven)

    def test_embedding_identity_is_unchanged_lazy_singleton_and_bounded(self) -> None:
        embedding = self.profile.embedding
        self.assertEqual(embedding.model_id, "intfloat/multilingual-e5-small")
        self.assertTrue(embedding.lazy_load)
        self.assertEqual(embedding.maximum_instances, 1)
        self.assertEqual(embedding.model_processes, 1)
        self.assertEqual(embedding.batch_size, 8)
        self.assertEqual(embedding.hard_batch_limit, 64)

    def test_memory_budget_leaves_host_headroom(self) -> None:
        budget = self.profile.host_budget
        self.assertEqual(budget.nominal_host_mib, 4096)
        self.assertGreaterEqual(budget.os_headroom_mib, 700)
        self.assertLess(budget.runtime_idle_budget_mib, budget.runtime_steady_budget_mib)
        self.assertLess(budget.runtime_steady_budget_mib, budget.runtime_peak_budget_mib)
        self.assertLess(
            budget.runtime_peak_budget_mib,
            budget.hard_pressure_observed_usage_mib,
        )
        self.assertLess(
            budget.hard_pressure_observed_usage_mib,
            budget.minimum_detected_host_mib,
        )
        self.assertLessEqual(
            budget.runtime_peak_budget_mib + budget.os_headroom_mib,
            budget.nominal_host_mib,
        )

    def test_database_pools_remain_small_and_separate(self) -> None:
        database = self.profile.database
        self.assertEqual(database.application_pool_max, 4)
        self.assertEqual(database.commit_helper_pool_max, 1)
        self.assertEqual(database.audit_pool_max, 1)
        self.assertEqual(database.review_pool_max, 1)
        self.assertEqual(database.maximum_connections, 7)
        self.assertNotEqual(
            database.application_pool_max,
            database.commit_helper_pool_max,
        )

    def test_all_queue_thread_and_cache_bounds_are_explicit(self) -> None:
        self.assertEqual(dataclasses.asdict(self.profile.queues), {
            "provider": 2,
            "embedding": 4,
            "critic": 1,
            "ingestion": 1,
            "review": 4,
            "audit": 16,
            "export": 1,
        })
        self.assertEqual(self.profile.threads.blocking_executor_max, 4)
        self.assertEqual(self.profile.threads.embedding_intraop, 1)
        self.assertEqual(self.profile.threads.omp, 1)
        self.assertEqual(self.profile.threads.mkl, 1)
        self.assertFalse(self.profile.threads.tokenizer_parallelism)
        self.assertFalse(self.profile.cache.authoritative)
        self.assertTrue(self.profile.cache.rebuildable)
        self.assertLessEqual(self.profile.cache.in_memory_cache_max_mib, 64)

    def test_startup_order_is_deterministic_and_keeps_heavy_optional_work_idle(self) -> None:
        self.assertEqual(self.profile.startup.order[-3:], (
            "KEEP_EMBEDDING_UNLOADED",
            "KEEP_CRITIC_DISABLED",
            "KEEP_INGESTION_DISABLED",
        ))
        self.assertEqual(len(set(self.profile.startup.order)), len(self.profile.startup.order))

    def test_resource_profile_has_zero_authority(self) -> None:
        self.assertFalse(any(dataclasses.asdict(self.profile.authority).values()))
        self.assertTrue(self.profile.logging.security_logging_enabled)
        self.assertFalse(self.profile.logging.large_payload_logging)

    def test_deep_mutation_is_rejected(self) -> None:
        object.__setattr__(self.profile.embedding, "batch_size", 64)
        with self.assertRaises(IntegrityError):
            verify_runtime_4gb_profile(self.profile)

    def test_unknown_duplicate_and_machine_specific_fields_fail_closed(self) -> None:
        raw = json.loads(DEFAULT_PROFILE_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unknown = dict(raw)
            unknown["unexpected"] = True
            unknown_path = root / "unknown.json"
            unknown_path.write_text(json.dumps(unknown), encoding="utf-8")
            with self.assertRaises(ContractValidationError):
                load_runtime_4gb_profile(unknown_path)

            duplicate_path = root / "duplicate.json"
            duplicate_path.write_text(
                DEFAULT_PROFILE_PATH.read_text(encoding="utf-8").replace(
                    '"schema_version": "1.0.0",',
                    '"schema_version": "1.0.0", "schema_version": "1.0.0",',
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ContractValidationError):
                load_runtime_4gb_profile(duplicate_path)

            machine_path = dict(raw)
            machine_path["profile_id"] = "/home/example/profile"
            machine_path_file = root / "path.json"
            machine_path_file.write_text(json.dumps(machine_path), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_runtime_4gb_profile(machine_path_file)

    def test_noncanonical_forged_profile_digest_is_rejected(self) -> None:
        values = {
            field.name: getattr(self.profile, field.name)
            for field in dataclasses.fields(Runtime4GBProfile)
        }
        values["profile_digest"] = "0" * 64
        with self.assertRaises(IntegrityError):
            Runtime4GBProfile(**values)

    def test_canonical_step40_resource_evidence_is_bound_and_secret_free(self) -> None:
        raw = STEP40_EVIDENCE.read_bytes()
        evidence = json.loads(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), STEP40_EVIDENCE_FILE_SHA256)
        self.assertEqual(evidence["validation_digest"], STEP40_VALIDATION_DIGEST)
        self.assertEqual(
            evidence["validation_digest"],
            canonical_sha256(evidence, exclude_fields=("validation_digest",)),
        )
        self.assertEqual(raw, (canonical_json(evidence) + "\n").encode("utf-8"))
        self.assertEqual(evidence["status"], "PASS_4GB_CONTROLLED")
        self.assertTrue(evidence["closure_eligible"])
        self.assertTrue(evidence["budget_result"]["within_budget"])
        self.assertLessEqual(
            evidence["budget_result"]["conservative_core_peak_mib"],
            evidence["budget_result"]["configured_peak_budget_mib"],
        )
        self.assertEqual(evidence["embedding"]["instance_count"], 1)
        self.assertTrue(evidence["embedding"]["lazy_load"])
        self.assertFalse(evidence["step41_started"])
        assert_secret_free(
            evidence,
            surface="Step40 committed resource evidence",
            reject_machine_paths=True,
        )


if __name__ == "__main__":
    unittest.main()
