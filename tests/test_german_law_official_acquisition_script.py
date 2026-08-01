"""Guard, dispatch, report, and resume tests for the acquisition CLI."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aioa_memory_kernel.acquisition import AcquisitionPolicy, SourceStatus
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
)
from scripts import run_german_law_official_acquisition as runner


class _Writer:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.payload = bytearray()

    def __enter__(self):
        return self

    def write(self, payload: bytes) -> None:
        self.payload.extend(payload)

    def publish(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(bytes(self.payload))
        return hashlib.sha256(self.payload).hexdigest(), len(self.payload)

    def __exit__(self, *_arguments: object) -> None:
        return None


class _ReportRoot:
    def __init__(self, base: Path) -> None:
        self.root = base
        self.policy = AcquisitionPolicy()
        self.status = SimpleNamespace(
            device_reference=self.policy.expected_device_reference
        )
        self.request_count = 7
        for relative, payload in (
            ("00_CONTROL/request-ledger.jsonl", b""),
            ("00_CONTROL/object-ledger.jsonl", b""),
            ("03_SOURCE_CATALOG/official-source-catalog.jsonl", b"{}\n"),
        ):
            path = self.resolve(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

    def resolve(self, relative: str) -> Path:
        return self.root / relative

    def require_regular_file(self, relative: str) -> Path:
        path = self.resolve(relative)
        if not path.is_file() or path.is_symlink():
            raise AssertionError("unsafe synthetic report file")
        return path

    def stream_writer(self, relative: str) -> _Writer:
        path = self.resolve(relative)
        if path.exists():
            raise AssertionError("test writer would overwrite")
        return _Writer(path)

    def write_json_absent(self, relative: str, value: object) -> None:
        with self.stream_writer(relative) as writer:
            writer.write(canonical_json_bytes(value) + b"\n")
            writer.publish()

    def root_size(self) -> int:
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def free_bytes(self) -> int:
        return 400 * 1024**3


class AcquisitionRunnerTests(unittest.TestCase):
    def test_source_order_finishes_short_terminal_sources_before_long_sources(self) -> None:
        self.assertEqual(
            runner.SOURCE_ORDER,
            ("bayern", "bmf", "dip", "eurlex", "bremen", "gii", "bgbl"),
        )

    def test_arguments_require_exact_mode_sources_and_bounded_runtime(self) -> None:
        parsed = runner._arguments(
            [
                "--resume",
                "--sources",
                "bgbl,bayern,gii",
                "--max-runtime-seconds",
                "600",
            ]
        )
        self.assertTrue(parsed.resume)
        self.assertEqual(parsed.sources, ("bayern", "gii", "bgbl"))
        for arguments in (
            [],
            ["--resume", "--sources", "unknown"],
            ["--resume", "--max-runtime-seconds", "0"],
            ["--record-compatible-repair"],
            [
                "--resume",
                "--repair-reason",
                runner.COMPATIBLE_REPAIR_REASON,
            ],
        ):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                runner._arguments(arguments)

    def test_dirty_scope_is_fixed_and_rejects_unrelated_paths(self) -> None:
        self.assertTrue(
            runner._dirty_path_allowed(
                "src/aioa_memory_kernel/acquisition/http.py"
            )
        )
        self.assertTrue(
            runner._dirty_path_allowed(
                "tests/test_official_acquisition_http.py"
            )
        )
        for path in ("README.md", "src/unrelated.py", "tests/test_other.py"):
            self.assertFalse(runner._dirty_path_allowed(path))

    def test_live_repository_guard_accepts_only_the_intended_dirty_scope(self) -> None:
        state = runner._repository_guard()
        self.assertEqual(state["repository_head"], runner.EXPECTED_BASELINE)
        self.assertEqual(state["origin_main"], runner.EXPECTED_BASELINE)
        self.assertTrue(state["dirty_paths"])
        self.assertRegex(str(state["worktree_digest"]), r"^[0-9a-f]{64}$")

    def test_fixed_dispatch_never_uses_manifest_or_user_attribute_names(self) -> None:
        calls: list[str] = []

        class Acquisition:
            def __getattribute__(self, name: str):
                if name.startswith("run_"):
                    return lambda: calls.append(name) or {"status": "COMPLETE"}
                return super().__getattribute__(name)

        acquisition = Acquisition()
        for token in runner.SOURCE_ORDER:
            runner._invoke_source(acquisition, token)
        self.assertEqual(len(calls), len(runner.SOURCE_ORDER))
        with self.assertRaises(runner.OfficialAcquisitionScriptError):
            runner._invoke_source(acquisition, "manifest_supplied_method")

    def test_partial_report_is_versioned_and_contains_exact_safe_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _ReportRoot(Path(temporary))
            result = runner._write_reports(
                root=root,
                run_id="synthetic-partial",
                mode="RESUME",
                started_at="2030-01-01T00:00:00Z",
                guard_state={
                    "repository_head": runner.EXPECTED_BASELINE,
                    "worktree_digest": "a" * 64,
                },
                selected_sources=("gii", "bgbl"),
                maximum_runtime_seconds=600,
                results={
                    runner.GII_ID: {"status": SourceStatus.COMPLETE.value},
                    runner.BGBL_ID: {"status": SourceStatus.PARTIAL.value},
                },
                resume_sources=("bgbl",),
            )
            self.assertEqual(
                result["status"],
                "AUTHORIZED_ACQUISITION_PARTIAL_SAFE_RESUME_REQUIRED",
            )
            self.assertEqual(
                result["safe_resume_command"],
                "python3 scripts/run_german_law_official_acquisition.py "
                "--resume --sources bgbl --max-runtime-seconds 600",
            )
            self.assertTrue(
                root.resolve(
                    "99_REPORTS/acquisition-summary-synthetic-partial.json"
                ).is_file()
            )
            self.assertFalse(
                root.resolve("99_REPORTS/acquisition-summary.json").exists()
            )

    def test_complete_report_writes_exact_final_names_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _ReportRoot(Path(temporary))
            result = runner._write_reports(
                root=root,
                run_id="synthetic-complete",
                mode="INITIALIZE",
                started_at="2030-01-01T00:00:00Z",
                guard_state={
                    "repository_head": runner.EXPECTED_BASELINE,
                    "worktree_digest": "b" * 64,
                },
                selected_sources=("bayern",),
                maximum_runtime_seconds=600,
                results={
                    runner.BAYERN_ID: {
                        "status": SourceStatus.SKIPPED_SAFE.value
                    }
                },
                resume_sources=(),
            )
            self.assertEqual(
                result["status"], "AUTHORIZED_ACQUISITION_COMPLETE"
            )
            for name in (
                "acquisition-summary.json",
                "acquisition-report.md",
                "coverage-matrix.json",
                "missing-and-blocked-sources.json",
                "refresh-plan.json",
                "resume-status.json",
            ):
                self.assertTrue(root.resolve(f"99_REPORTS/{name}").is_file())

    def test_public_reports_reject_absolute_machine_paths(self) -> None:
        for value in (
            {"path": "/media/l/device"},
            {"path": "/home/l/private"},
            {"config": ".local/external-data.env"},
        ):
            with self.subTest(value=value), self.assertRaises(
                runner.OfficialAcquisitionScriptError
            ):
                runner._assert_sanitized(value)

    def test_explicit_compatible_repair_receipt_binds_resume_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _ReportRoot(Path(temporary))
            old_digest = "1" * 64
            worktree_files = [
                {
                    "relative_path": (
                        "src/aioa_memory_kernel/german_law/acquisition.py"
                    ),
                    "byte_length": 7,
                    "sha256": "2" * 64,
                }
            ]
            new_digest = canonical_sha256(worktree_files)
            root.write_json_absent(
                "00_CONTROL/run-state.json",
                {
                    "repository_head": runner.EXPECTED_BASELINE,
                    "worktree_digest": old_digest,
                },
            )
            prior = {
                "schema_version": "1.0.0",
                "reason": "BGBL_SUFFIXED_OFFICIAL_ISSUE_IDENTIFIERS",
                "repository_head": runner.EXPECTED_BASELINE,
                "previous_worktree_digest": old_digest,
                "compatible_worktree_digest": "3" * 64,
            }
            prior["repair_digest"] = canonical_sha256(prior)
            root.write_json_absent(
                "00_CONTROL/checkpoints/"
                f"worktree-repair-{old_digest[:16]}-{'3' * 16}.json",
                prior,
            )
            guard_state = {
                "repository_head": runner.EXPECTED_BASELINE,
                "worktree_digest": new_digest,
                "dirty_paths": [
                    "src/aioa_memory_kernel/german_law/acquisition.py"
                ],
                "dirty_file_records": worktree_files,
            }

            receipt = runner._record_compatible_worktree_repair(
                root,
                guard_state,
                reason=runner.COMPATIBLE_REPAIR_REASON,
                confirm_previous_worktree_digest=old_digest,
                confirm_compatible_worktree_digest=new_digest,
                confirm_worktree_content_manifest_digest=new_digest,
                confirm_prior_repair_digest=prior["repair_digest"],
            )
            self.assertEqual(
                receipt["reason"],
                runner.COMPATIBLE_REPAIR_REASON,
            )
            runner._verify_resume_binding(root, guard_state)
            self.assertEqual(receipt["corpus_https_requests"], 0)
            self.assertEqual(receipt["download_writes"], 0)
            self.assertEqual(receipt["control_evidence_writes"], 1)
            self.assertFalse(receipt["orphan_parts_reconciled"])

            with self.assertRaises(runner.OfficialAcquisitionScriptError):
                runner._record_compatible_worktree_repair(
                    root,
                    {**guard_state, "worktree_digest": "4" * 64},
                    reason=runner.COMPATIBLE_REPAIR_REASON,
                    confirm_previous_worktree_digest=old_digest,
                    confirm_compatible_worktree_digest=new_digest,
                    confirm_worktree_content_manifest_digest=new_digest,
                    confirm_prior_repair_digest=prior["repair_digest"],
                )


if __name__ == "__main__":
    unittest.main()
