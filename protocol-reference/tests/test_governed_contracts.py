# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from governed_contracts import (
    CONTRACT_VERSION, GovernedContractViolation, assert_instruction_binding,
    compute_payload_digest, verify_instruction_payload_digest,
    validate_decision_envelope, validate_instruction_envelope,
)


class GovernedContractTests(unittest.TestCase):
    def instruction(self, **changes):
        value = {
            "contract_version": CONTRACT_VERSION, "request_id": "req-001",
            "operation_id": "op-001", "idempotency_key": "idem-key-001",
            "principal_ref": "principal:institution-user-7",
            "authority_ref": "authority-check:abc", "action": "submit_transfer",
            "target_ref": "account:masked-reference", "purpose": "approved supplier payment",
            "jurisdiction": "UG", "submitted_at": "2026-10-27T10:00:00Z",
            "payload_digest": "sha256:" + ("a" * 64),
            "rule_pack_version": "ug-payments-2026-10",
            "evidence_refs": ["evidence:invoice-001"],
            "provider_profile": "conventional-server",
        }
        value.update(changes)
        return value

    def decision(self, **changes):
        value = {
            "contract_version": CONTRACT_VERSION, "request_id": "req-001",
            "operation_id": "op-001", "protocol_decision": "PASSED",
            "reason_code": "REQUIRED_CHECKS_PASSED",
            "user_message": "The required checks passed. The authorised next step may proceed.",
            "rule_pack_version": "ug-payments-2026-10",
            "minah_disposition_ref": "minah:disp-001", "cee_execution_ref": "cee:exec-001",
            "external_effect_status": "NOT_ATTEMPTED",
            "evidence_refs": ["evidence:invoice-001"],
        }
        value.update(changes)
        return value

    def test_valid_instruction_is_detached(self):
        source = self.instruction()
        checked = validate_instruction_envelope(source)
        source["action"] = "tampered"
        self.assertEqual(checked["action"], "submit_transfer")

    def test_unknown_version_and_missing_fields_fail_closed(self):
        with self.assertRaisesRegex(GovernedContractViolation, "CONTRACT_VERSION_MISMATCH"):
            validate_instruction_envelope(self.instruction(contract_version="unknown"))
        with self.assertRaisesRegex(GovernedContractViolation, "MISSING_FIELDS"):
            validate_instruction_envelope({"contract_version": CONTRACT_VERSION})

    def test_instruction_rejects_naive_time_and_bad_digest(self):
        with self.assertRaisesRegex(GovernedContractViolation, "MUST_INCLUDE_TIMEZONE"):
            validate_instruction_envelope(self.instruction(submitted_at="2026-10-27T10:00:00"))
        with self.assertRaisesRegex(GovernedContractViolation, "PAYLOAD_DIGEST_MUST_DECLARE_ALGORITHM"):
            validate_instruction_envelope(self.instruction(payload_digest="abcdef"))
        with self.assertRaisesRegex(GovernedContractViolation, "INVALID_PAYLOAD_DIGEST"):
            validate_instruction_envelope(self.instruction(payload_digest="sha256:" + ("g" * 64)))


    def test_canonical_payload_digest_detects_envelope_tampering(self):
        instruction = self.instruction()
        instruction["payload_digest"] = compute_payload_digest(instruction)
        checked = verify_instruction_payload_digest(instruction)
        self.assertEqual(checked["payload_digest"], instruction["payload_digest"])
        changed = {**instruction, "authority_ref": "authority-check:changed"}
        with self.assertRaisesRegex(GovernedContractViolation, "PAYLOAD_DIGEST_MISMATCH"):
            verify_instruction_payload_digest(changed)

    def test_canonical_payload_digest_covers_execution_evidence_extension(self):
        instruction = self.instruction(evidence={"commodity": "SYNTHETIC_MAIZE"})
        instruction["payload_digest"] = compute_payload_digest(instruction)
        verify_instruction_payload_digest(instruction)
        changed = {**instruction, "evidence": {"commodity": "SYNTHETIC_GOLD"}}
        with self.assertRaisesRegex(GovernedContractViolation, "PAYLOAD_DIGEST_MISMATCH"):
            verify_instruction_payload_digest(changed)

    def test_pass_does_not_mean_external_effect_completed(self):
        result = validate_decision_envelope(self.decision())
        self.assertEqual(result["protocol_decision"], "PASSED")
        self.assertEqual(result["external_effect_status"], "NOT_ATTEMPTED")

    def test_confirmed_effect_requires_separate_confirmation_reference(self):
        with self.assertRaisesRegex(GovernedContractViolation, "CONFIRMATION_REF"):
            validate_decision_envelope(self.decision(external_effect_status="CONFIRMED"))
        result = validate_decision_envelope(self.decision(
            external_effect_status="CONFIRMED", effect_confirmation_ref="provider-event:xyz"
        ))
        self.assertEqual(result["effect_confirmation_ref"], "provider-event:xyz")

    def test_rejected_or_deferred_cannot_claim_confirmed_effect(self):
        for status in ("REJECTED", "DEFERRED", "UNKNOWN"):
            with self.subTest(status=status):
                with self.assertRaisesRegex(GovernedContractViolation, "CANNOT_CLAIM_CONFIRMED"):
                    validate_decision_envelope(self.decision(
                        protocol_decision=status, external_effect_status="CONFIRMED",
                        effect_confirmation_ref="provider-event:xyz",
                    ))

    def test_decision_must_bind_to_same_request_operation_and_rule_version(self):
        assert_instruction_binding(self.instruction(), self.decision())
        with self.assertRaisesRegex(GovernedContractViolation, "OPERATION_ID_MISMATCH"):
            assert_instruction_binding(self.instruction(), self.decision(operation_id="other-op"))


    def test_non_string_decision_statuses_fail_as_contract_errors(self):
        for field, value, expected in (
            ("protocol_decision", ["PASSED"], "INVALID_PROTOCOL_DECISION"),
            ("external_effect_status", {"state": "CONFIRMED"}, "INVALID_EXTERNAL_EFFECT_STATUS"),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(GovernedContractViolation, expected):
                    validate_decision_envelope(self.decision(**{field: value}))

    def test_required_minah_and_cee_references(self):
        with self.assertRaisesRegex(GovernedContractViolation, "MISSING_OR_INVALID_CEE_EXECUTION_REF"):
            validate_decision_envelope(self.decision(cee_execution_ref=""))


if __name__ == "__main__":
    unittest.main()
