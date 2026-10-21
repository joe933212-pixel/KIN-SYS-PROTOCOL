# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
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
            "concurrent-identical",
            "process-death-before-commit",
            "postcommit-before-ack",
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
        self.assertEqual(len(report["comparisons"]), 9)

    def test_concurrent_identical_requests_match(self):
        comparison = compare_scenario("concurrent-identical")
        self.assertTrue(comparison["equivalent_on_observed_fields"], comparison["differences"])
        result = comparison["results"]["kin_sys"]
        self.assertEqual(result["accepted_count"], 2)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["history_count"], 1)


    def test_process_death_before_commit_leaves_no_partial_effect_in_both(self):
        comparison = compare_scenario("process-death-before-commit")
        self.assertTrue(comparison["equivalent_on_observed_fields"], comparison["differences"])
        for result in comparison["results"].values():
            self.assertEqual(result["status"], "INTERRUPTED")
            self.assertEqual(result["state"], "PENDING")
            self.assertEqual(result["version"], 0)
            self.assertEqual(result["history_count"], 0)
            self.assertIsNone(result["event_outcome"])

    def test_postcommit_lost_ack_retry_is_idempotent_in_both(self):
        comparison = compare_scenario("postcommit-before-ack")
        self.assertTrue(comparison["equivalent_on_observed_fields"], comparison["differences"])
        for result in comparison["results"].values():
            self.assertEqual(result["status"], "COMMITTED_ACK_LOST")
            self.assertTrue(result["retry_duplicate"])
            self.assertEqual(result["state"], "ACTIVE")
            self.assertEqual(result["version"], 1)
            self.assertEqual(result["history_count"], 1)
            self.assertEqual(result["event_outcome"], "ACCEPTED")

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "UNKNOWN_SCENARIO"):
            run_scenario("kin-sys-sqlite", "not-a-scenario")


if __name__ == "__main__":
    unittest.main(verbosity=2)
