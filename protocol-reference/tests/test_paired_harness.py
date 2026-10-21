import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comparators.paired_harness import compare_scenario, run_all_scenarios, run_scenario


class PairedHarnessTests(unittest.TestCase):
    def test_core_scenarios_match_across_both_implementations(self):
        for scenario in (
            "accepted",
            "duplicate",
            "conflicting-id",
            "wrong-authority",
            "rule-version-mismatch",
            "precommit-failure",
        ):
            with self.subTest(scenario=scenario):
                comparison = compare_scenario(scenario)
                self.assertTrue(
                    comparison["equivalent_on_observed_fields"],
                    comparison["differences"],
                )

    def test_normalized_accepted_result_contains_required_observables(self):
        result = run_scenario("kin-sys-sqlite", "accepted")
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["state"], "ACTIVE")
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["history_count"], 1)
        self.assertEqual(result["event_outcome"], "ACCEPTED")

    def test_run_all_scenarios_reports_equivalence(self):
        report = run_all_scenarios()
        self.assertTrue(report["all_equivalent_on_observed_fields"])
        self.assertEqual(len(report["comparisons"]), 6)

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "UNKNOWN_SCENARIO"):
            run_scenario("kin-sys-sqlite", "not-a-scenario")


if __name__ == "__main__":
    unittest.main(verbosity=2)
