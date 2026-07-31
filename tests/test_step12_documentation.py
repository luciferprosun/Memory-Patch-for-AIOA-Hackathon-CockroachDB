from pathlib import Path
import unittest
from tests._support import REPOSITORY_ROOT

class Step12DocumentationTests(unittest.TestCase):
    def test_required_documents_exist_and_preserve_boundary(self):
        paths=[
            "docs/adr/ADR-019-trusted-hat-registry-runtime-boundary.md",
            "docs/architecture/HAT_REGISTRY_MANIFEST_RUNTIME_BOUNDARY_1A.md",
            "docs/operations/STEP_12_HAT_REGISTRY_LIVE_VALIDATION_1A.md",
            "docs/audits/STEP_12_HAT_REGISTRY_MANIFEST_RUNTIME_BOUNDARY_CLOSURE_1A.md",
        ]
        combined="\n".join((REPOSITORY_ROOT/path).read_text() for path in paths)
        for token in ("zero-authority", "not a sandbox", "Personal Memory", "Step 13 was not started"):
            self.assertIn(token,combined)
    def test_no_dynamic_loading_implementation(self):
        source="\n".join(path.read_text() for path in (REPOSITORY_ROOT/"src/aioa_memory_kernel/hats").glob("*.py"))
        for token in ("importlib", "entry_points", "subprocess", "eval(", "exec(", "pickle", "ctypes"):
            self.assertNotIn(token,source)
