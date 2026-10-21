import unittest

from protocol_reference.transition import EventIdConflict, ProtocolStore


class TransitionContractTests(unittest.TestCase):
    def setUp(self):
        self.store = ProtocolStore()
        self.store.create_process(process_id="P-001")

    def tearDown(self):
        self.store.close()

    def event(self, **overrides):
        event = {
            "event_id": "E-001",
            "process_id": "P-001",
            "actor_id": "AUTHORITY-001",
            "event_type": "ACTIVATE",
            "rule_set": "LICENCE-RULES",
            "rule_version": "1.0",
        }
        event.update(overrides)
        return event

    def test_authorised_transition_commits_state_and_history(self):
        result = self.store.execute(self.event())
        self.assertEqual(result.status, "ACCEPTED")
        self.assertEqual(self.store.process("P-001")["state"], "ACTIVE")
        history = self.store.history("P-001")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["event_id"], "E-001")
        self.assertEqual(history[0]["rule_version"], "1.0")

    def test_identical_duplicate_does_not_transition_twice(self):
        self.store.execute(self.event())
        before = self.store.process("P-001")["state_version"]
        result = self.store.execute(self.event())
        self.assertTrue(result.duplicate)
        self.assertEqual(self.store.process("P-001")["state_version"], before)
        self.assertEqual(len(self.store.history("P-001")), 1)

    def test_event_id_reuse_with_different_content_is_conflict(self):
        self.store.execute(self.event())
        with self.assertRaises(EventIdConflict):
            self.store.execute(self.event(actor_id="SOMEONE-ELSE"))
        self.assertEqual(self.store.process("P-001")["state"], "ACTIVE")
        self.assertEqual(len(self.store.history("P-001")), 1)

    def test_wrong_authority_is_audited_without_state_change(self):
        result = self.store.execute(self.event(actor_id="UNAUTHORISED"))
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.reason, "AUTHORITY_MISMATCH")
        self.assertEqual(self.store.process("P-001")["state"], "PENDING")
        self.assertEqual(self.store.event("E-001")["outcome"], "REJECTED")
        self.assertEqual(self.store.history("P-001"), [])

    def test_revoked_authority_is_rejected(self):
        self.store.set_authority_active("P-001", False)
        result = self.store.execute(self.event())
        self.assertEqual(result.reason, "AUTHORITY_REVOKED")
        self.assertEqual(self.store.process("P-001")["state"], "PENDING")

    def test_false_rule_is_audited_without_state_change(self):
        self.store.set_rule("P-001", version="1.0", eligible=False)
        result = self.store.execute(self.event())
        self.assertEqual(result.reason, "RULE_REJECTED")
        self.assertIsNotNone(self.store.event("E-001"))
        self.assertEqual(self.store.history("P-001"), [])

    def test_rule_version_mismatch_is_rejected(self):
        result = self.store.execute(self.event(rule_version="0.9"))
        self.assertEqual(result.reason, "RULE_VERSION_MISMATCH")
        self.assertEqual(self.store.process("P-001")["state"], "PENDING")

    def test_failure_before_commit_rolls_back_state_event_and_history(self):
        with self.assertRaisesRegex(RuntimeError, "INJECTED_FAILURE_BEFORE_COMMIT"):
            self.store.execute(self.event(), fail_before_commit=True)
        self.assertEqual(self.store.process("P-001")["state"], "PENDING")
        self.assertEqual(self.store.process("P-001")["state_version"], 0)
        self.assertIsNone(self.store.event("E-001"))
        self.assertEqual(self.store.history("P-001"), [])

    def test_later_rule_change_does_not_rewrite_history(self):
        self.store.execute(self.event())
        self.store.set_rule("P-001", version="2.0", eligible=False)
        history = self.store.history("P-001")
        self.assertEqual(history[0]["rule_version"], "1.0")

    def test_undefined_transition_is_rejected_and_audited(self):
        result = self.store.execute(self.event(event_type="DELETE"))
        self.assertEqual(result.reason, "UNDEFINED_TRANSITION")
        self.assertEqual(self.store.process("P-001")["state"], "PENDING")
        self.assertIsNotNone(self.store.event("E-001"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
