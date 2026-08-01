from __future__ import annotations

import unittest
from tests._support import REPOSITORY_ROOT


class GermanLawValidationScriptTests(unittest.TestCase):
    def test_authority_assertion_compares_promulgation_to_consolidation(self):
        text=(REPOSITORY_ROOT/"scripts/run_german_law_hat_validation.py").read_text()
        self.assertIn('ranked.index("promulgation") >= ranked.index("consolidation")',text)
        self.assertNotIn('ranked[0] != "promulgation"',text)

    def test_controlled_boundaries_are_zero(self):
        text=(REPOSITORY_ROOT/"scripts/run_german_law_hat_validation.py").read_text()
        for token in ('"aws_writes":0','"s3_writes":0','"external_volume_writes":0','"corpus_reads":0','"corpus_writes":0','"model_calls":0'):
            self.assertIn(token,text)

    def test_cleanup_rejects_force_kill(self):
        text=(REPOSITORY_ROOT/"scripts/run_german_law_hat_validation.py").read_text()
        self.assertIn('cleanup.get("force_kill_used")',text)
        self.assertIn('runtime.graceful_stop_and_remove',text)


if __name__ == "__main__": unittest.main()
