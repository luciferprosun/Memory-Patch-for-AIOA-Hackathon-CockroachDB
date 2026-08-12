from __future__ import annotations

import json
import unittest
from pathlib import Path

from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.runtime import (
    ComponentState,
    build_runtime_health_snapshot,
    load_runtime_4gb_profile,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STEP38_EVIDENCE = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "e2e"
    / "step38-german-law-full-e2e-validation.json"
)


class Step40OptionalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_runtime_4gb_profile()
        self.required_ready = {
            "profile": self.profile,
            "process_responsive": True,
            "external_volume_ready": True,
            "database_schema_ready": True,
            "german_law_corpus_ready": True,
            "personal_memory_persistence_ready": True,
            "audit_append_ready": True,
            "provider_configuration_ready": True,
            "owner_ui_ready": True,
            "embedding_loaded": False,
        }

    @staticmethod
    def states(snapshot) -> dict[str, ComponentState]:
        return {item.component_id: item.state for item in snapshot.components}

    def test_critic_disabled_is_intentional_and_core_is_ready(self) -> None:
        snapshot = build_runtime_health_snapshot(**self.required_ready)
        self.assertTrue(snapshot.liveness)
        self.assertTrue(snapshot.readiness)
        self.assertEqual(
            self.states(snapshot)["critic"],
            ComponentState.DISABLED_INTENTIONAL,
        )

    def test_critic_provider_unavailable_does_not_break_core_readiness(self) -> None:
        snapshot = build_runtime_health_snapshot(
            **self.required_ready,
            critic_enabled=True,
            critic_available=False,
        )
        self.assertTrue(snapshot.readiness)
        critic = next(
            item for item in snapshot.components if item.component_id == "critic"
        )
        self.assertFalse(critic.required)
        self.assertFalse(critic.ready)
        self.assertEqual(critic.state, ComponentState.UNAVAILABLE_OPTIONAL)

    def test_critic_enabled_is_separately_visible(self) -> None:
        snapshot = build_runtime_health_snapshot(
            **self.required_ready,
            critic_enabled=True,
            critic_available=True,
        )
        self.assertTrue(snapshot.readiness)
        self.assertEqual(self.states(snapshot)["critic"], ComponentState.READY)

    def test_embedding_unloaded_at_idle_is_not_readiness_failure(self) -> None:
        snapshot = build_runtime_health_snapshot(**self.required_ready)
        self.assertTrue(snapshot.readiness)
        self.assertEqual(
            self.states(snapshot)["embedding"],
            ComponentState.UNLOADED_LAZY,
        )

    def test_ingestion_disabled_after_prepared_corpus_is_not_failure(self) -> None:
        snapshot = build_runtime_health_snapshot(**self.required_ready)
        self.assertTrue(snapshot.readiness)
        self.assertEqual(
            self.states(snapshot)["ingestion"],
            ComponentState.DISABLED_INTENTIONAL,
        )
        self.assertTrue(self.profile.optional_services.ingestion_requires_prepared_corpus)

    def test_required_database_corpus_audit_and_owner_ui_fail_readiness(self) -> None:
        for field in (
            "database_schema_ready",
            "german_law_corpus_ready",
            "personal_memory_persistence_ready",
            "audit_append_ready",
            "owner_ui_ready",
        ):
            with self.subTest(field=field):
                inputs = dict(self.required_ready)
                inputs[field] = False
                snapshot = build_runtime_health_snapshot(**inputs)
                self.assertFalse(snapshot.readiness)
                self.assertTrue(snapshot.liveness)

    def test_liveness_is_cheap_and_does_not_claim_semantic_validation(self) -> None:
        snapshot = build_runtime_health_snapshot(**self.required_ready)
        self.assertFalse(snapshot.probe_performed_model_call)
        self.assertFalse(snapshot.probe_performed_full_e2e)
        self.assertFalse(snapshot.probe_performed_audit_chain_verification)

    def test_process_unresponsive_fails_liveness_and_readiness(self) -> None:
        inputs = dict(self.required_ready)
        inputs["process_responsive"] = False
        snapshot = build_runtime_health_snapshot(**inputs)
        self.assertFalse(snapshot.liveness)
        self.assertFalse(snapshot.readiness)

    def test_committed_step38_core_proof_remains_bound_and_critic_independent(self) -> None:
        evidence = json.loads(STEP38_EVIDENCE.read_text(encoding="utf-8"))
        claimed = evidence["validation_digest"]
        self.assertEqual(
            claimed,
            canonical_sha256(evidence, exclude_fields=("validation_digest",)),
        )
        self.assertEqual(evidence["status"], "PASS_LIVE_COHERENT_LINEAGE")
        self.assertTrue(evidence["closure_eligible"])
        self.assertEqual(
            evidence["real_model_flow"]["status"],
            "PASS_REAL_VERIFIED_LINEAGE",
        )
        self.assertEqual(
            evidence["coherent_runtime"]["status"],
            "PASS_REAL_COHERENT_PERSONAL_MEMORY_LINEAGE",
        )
        self.assertFalse(evidence["coherent_runtime"]["step39_started"])

    def test_ui_stack_has_no_second_frontend_runtime(self) -> None:
        requirements = (REPOSITORY_ROOT / "requirements-ui.txt").read_text(
            encoding="utf-8"
        )
        package = json.loads(
            (REPOSITORY_ROOT / "package.json").read_text(encoding="utf-8")
        )
        self.assertIn("fastapi==0.141.1", requirements)
        self.assertIn("Jinja2==3.1.6", requirements)
        self.assertIn("uvicorn==0.52.1", requirements)
        self.assertEqual(package["dependencies"], {"htmx.org": "2.0.8"})
        self.assertEqual(self.profile.process_layout.additional_frontend_processes, 0)


if __name__ == "__main__":
    unittest.main()
