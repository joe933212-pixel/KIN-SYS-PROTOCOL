# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Paired whole-flow tests for the synthetic integrated demonstrator contract."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from comparators.paired_integrated_harness import SCENARIOS, compare_scenario, run_all


class PairedIntegratedHarnessTests(unittest.TestCase):
    def test_all_declared_scenarios_match_on_observed_contract(self):
        report = run_all()
        self.assertTrue(report["all_equivalent_on_observed_fields"], report)
        self.assertEqual(report["scenario_count"], len(SCENARIOS))

    def test_each_scenario_has_no_observed_differences(self):
        for scenario in sorted(SCENARIOS):
            with self.subTest(scenario=scenario):
                result = compare_scenario(scenario)
                self.assertTrue(result["equivalent_on_observed_fields"], result["differences"])

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "UNKNOWN_INTEGRATED_SCENARIO"):
            compare_scenario("unlisted-case")


if __name__ == "__main__":
    unittest.main()
