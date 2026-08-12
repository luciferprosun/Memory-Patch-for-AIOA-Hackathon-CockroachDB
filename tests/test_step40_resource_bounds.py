from __future__ import annotations

import concurrent.futures
import dataclasses
import os
import threading
import time
import unittest

from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.runtime import (
    EmbeddingLoadBackpressured,
    LazyEmbeddingRuntime,
    ResourceDecisionCode,
    ResourceObservation,
    ResourcePressureGuard,
    ResourcePressureState,
    ResourceWorkKind,
    embedding_thread_environment,
    load_runtime_4gb_profile,
)
from scripts.measure_step40_runtime_resources import (
    measure_scenario,
    read_host_memory,
    sample_process_tree,
)


class Step40ResourceBoundsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_runtime_4gb_profile()
        self.guard = ResourcePressureGuard(self.profile)

    @staticmethod
    def observation(
        *,
        available: int = 1000,
        process_rss: int = 128,
    ) -> ResourceObservation:
        return ResourceObservation.create(
            host_total_mib=3751,
            host_available_mib=available,
            process_tree_rss_mib=process_rss,
        )

    def decision(
        self,
        kind: ResourceWorkKind,
        *,
        available: int = 1000,
        process_rss: int = 128,
        queue_depth: int = 0,
    ):
        return self.guard.evaluate(
            work_kind=kind,
            observation=self.observation(
                available=available,
                process_rss=process_rss,
            ),
            queue_depth=queue_depth,
        )

    def test_normal_pressure_permits_core_and_heavy_work(self) -> None:
        for kind in (ResourceWorkKind.REQUIRED_CORE, ResourceWorkKind.HEAVY_EMBEDDING):
            with self.subTest(kind=kind):
                decision = self.decision(kind)
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.reason_code, ResourceDecisionCode.PERMITTED)
                self.assertEqual(decision.pressure_state, ResourcePressureState.NORMAL)

    def test_soft_pressure_suppresses_critic_before_core(self) -> None:
        critic = self.decision(ResourceWorkKind.OPTIONAL_CRITIC, available=700)
        core = self.decision(ResourceWorkKind.REQUIRED_CORE, available=700)
        self.assertFalse(critic.allowed)
        self.assertEqual(
            critic.reason_code,
            ResourceDecisionCode.OPTIONAL_CRITIC_SUPPRESSED,
        )
        self.assertEqual(critic.pressure_state, ResourcePressureState.SOFT_PRESSURE)
        self.assertTrue(core.allowed)
        self.assertEqual(core.reason_code, ResourceDecisionCode.PERMITTED)

    def test_hard_pressure_applies_exact_degradation_order(self) -> None:
        expected = {
            ResourceWorkKind.OPTIONAL_CRITIC: ResourceDecisionCode.OPTIONAL_CRITIC_SUPPRESSED,
            ResourceWorkKind.OPTIONAL_INGESTION: ResourceDecisionCode.OPTIONAL_INGESTION_PAUSED,
            ResourceWorkKind.HEAVY_EMBEDDING: ResourceDecisionCode.EMBEDDING_BACKPRESSURE,
            ResourceWorkKind.LARGE_EXPORT: ResourceDecisionCode.EXPORT_BACKPRESSURE,
            ResourceWorkKind.REQUIRED_CORE: ResourceDecisionCode.CORE_REQUEST_FAILED_CLOSED,
        }
        for kind, reason in expected.items():
            with self.subTest(kind=kind):
                decision = self.decision(kind, available=300)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason_code, reason)
                self.assertEqual(
                    decision.pressure_state,
                    ResourcePressureState.HARD_PRESSURE,
                )

    def test_every_queue_rejects_at_its_explicit_capacity(self) -> None:
        for kind in ResourceWorkKind:
            with self.subTest(kind=kind):
                limit = self.profile.queues.limit_for(kind.value)
                decision = self.decision(kind, queue_depth=limit)
                self.assertFalse(decision.allowed)
                self.assertEqual(
                    decision.reason_code,
                    ResourceDecisionCode.QUEUE_CAPACITY_REACHED,
                )
                self.assertEqual(decision.queue_limit, limit)

    def test_resource_decision_has_no_authority_under_any_pressure(self) -> None:
        for available in (1000, 700, 300):
            for kind in ResourceWorkKind:
                with self.subTest(available=available, kind=kind):
                    decision = self.decision(kind, available=available)
                    self.assertFalse(decision.verifier_bypass)
                    self.assertFalse(decision.audit_bypass)
                    self.assertFalse(decision.rls_bypass)
                    self.assertFalse(decision.automatic_personal_memory_approval)
                    self.assertFalse(decision.route_override)
                    self.assertFalse(decision.source_authority_override)
                    self.assertFalse(decision.canonical_evidence_override)

    def test_resource_observation_and_decision_are_hash_bound(self) -> None:
        observation = self.observation()
        object.__setattr__(observation, "host_available_mib", 1)
        with self.assertRaises(IntegrityError):
            self.guard.evaluate(
                work_kind=ResourceWorkKind.REQUIRED_CORE,
                observation=observation,
                queue_depth=0,
            )

        decision = self.decision(ResourceWorkKind.REQUIRED_CORE)
        object.__setattr__(decision, "audit_bypass", True)
        with self.assertRaises((ValueError, IntegrityError)):
            type(decision)(
                **{
                    field.name: getattr(decision, field.name)
                    for field in dataclasses.fields(decision)
                }
            )

    def test_lazy_embedding_does_not_invoke_factory_at_idle(self) -> None:
        calls = 0

        def factory() -> object:
            nonlocal calls
            calls += 1
            return object()

        runtime = LazyEmbeddingRuntime(profile=self.profile, factory=factory)
        self.assertFalse(runtime.status().loaded)
        self.assertEqual(runtime.status().instance_count, 0)
        self.assertEqual(calls, 0)

    def test_first_embedding_request_loads_and_second_reuses_one_instance(self) -> None:
        calls = 0

        def factory() -> object:
            nonlocal calls
            calls += 1
            return object()

        runtime = LazyEmbeddingRuntime(profile=self.profile, factory=factory)
        first = runtime.get()
        second = runtime.get()
        self.assertIs(first, second)
        self.assertEqual(calls, 1)
        self.assertTrue(runtime.status().loaded)
        self.assertEqual(runtime.status().instance_count, 1)

    def test_concurrent_embedding_requests_converge_to_one_instance(self) -> None:
        calls = 0
        call_lock = threading.Lock()

        def factory() -> object:
            nonlocal calls
            with call_lock:
                calls += 1
            time.sleep(0.02)
            return object()

        runtime = LazyEmbeddingRuntime(profile=self.profile, factory=factory)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            instances = tuple(executor.map(lambda _: runtime.get(), range(8)))
        self.assertTrue(all(item is instances[0] for item in instances))
        self.assertEqual(calls, 1)
        self.assertEqual(runtime.status().instance_count, 1)

    def test_embedding_batch_above_profile_limit_is_rejected_before_load(self) -> None:
        calls = 0

        def factory() -> object:
            nonlocal calls
            calls += 1
            return object()

        runtime = LazyEmbeddingRuntime(profile=self.profile, factory=factory)
        with self.assertRaises(ContractValidationError):
            runtime.get(batch_size=self.profile.embedding.batch_size + 1)
        self.assertEqual(calls, 0)

    def test_pressure_backpressures_embedding_before_model_load(self) -> None:
        calls = 0

        def factory() -> object:
            nonlocal calls
            calls += 1
            return object()

        decision = self.decision(ResourceWorkKind.HEAVY_EMBEDDING, available=300)
        runtime = LazyEmbeddingRuntime(profile=self.profile, factory=factory)
        with self.assertRaises(EmbeddingLoadBackpressured):
            runtime.get(guard_decision=decision)
        self.assertEqual(calls, 0)
        self.assertFalse(runtime.status().loaded)

    def test_embedding_thread_environment_is_bounded_and_process_local(self) -> None:
        environment = embedding_thread_environment(self.profile)
        self.assertEqual(dict(environment), {
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "TOKENIZERS_PARALLELISM": "false",
        })
        with self.assertRaises(TypeError):
            environment["OMP_NUM_THREADS"] = "8"

    def test_measurement_helper_accepts_no_arbitrary_command(self) -> None:
        with self.assertRaises(ValueError):
            measure_scenario("shell-command")

    def test_host_and_current_process_probes_are_numeric_and_bounded(self) -> None:
        host = read_host_memory()
        self.assertGreaterEqual(host["total_mib"], 1)
        self.assertGreaterEqual(host["available_mib"], 0)
        self.assertLessEqual(host["available_mib"], host["total_mib"])
        sample = sample_process_tree(os.getpid())
        self.assertGreaterEqual(sample.rss_kib, 1)
        self.assertGreaterEqual(sample.process_count, 1)
        self.assertGreaterEqual(sample.thread_count, 1)


if __name__ == "__main__":
    unittest.main()
