# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import unittest

from component_contracts import (
    RESPONSE_CONTRACT_VERSION,
    ContractViolation,
    accepted_data,
    make_component_response,
    validate_component_response,
)


class ComponentResponseContractTests(unittest.TestCase):
    def make_response(self, **overrides):
        fields = {
            "component_id": "minah_ai",
            "operation_id": "CASE-1:MINAH:REVIEW:1",
            "component_version": "minah-mock-0.1",
            "status": "ACCEPTED",
            "data": {"may_authorise": False, "status": "ADVISORY_ONLY"},
        }
        fields.update(overrides)
        return make_component_response(**fields)

    def test_valid_response_is_versioned_and_bound_to_component_and_operation(self):
        response = self.make_response()
        checked = validate_component_response(
            response,
            expected_component_id="minah_ai",
            expected_operation_id="CASE-1:MINAH:REVIEW:1",
        )
        self.assertEqual(checked["contract_version"], RESPONSE_CONTRACT_VERSION)
        self.assertEqual(accepted_data(checked)["may_authorise"], False)

    def test_wrong_component_or_operation_is_rejected(self):
        response = self.make_response()
        with self.assertRaisesRegex(ContractViolation, "COMPONENT_RESPONSE_IDENTITY_MISMATCH"):
            validate_component_response(
                response, expected_component_id="identity_authority",
                expected_operation_id="CASE-1:MINAH:REVIEW:1",
            )
        with self.assertRaisesRegex(ContractViolation, "COMPONENT_RESPONSE_OPERATION_MISMATCH"):
            validate_component_response(
                response, expected_component_id="minah_ai",
                expected_operation_id="CASE-2:MINAH:REVIEW:1",
            )

    def test_unknown_contract_version_and_malformed_envelope_fail_closed(self):
        response = self.make_response()
        response["contract_version"] = "future-or-unknown-contract"
        with self.assertRaisesRegex(ContractViolation, "COMPONENT_CONTRACT_VERSION_MISMATCH"):
            validate_component_response(
                response, expected_component_id="minah_ai",
                expected_operation_id="CASE-1:MINAH:REVIEW:1",
            )
        with self.assertRaisesRegex(ContractViolation, "MALFORMED_COMPONENT_RESPONSE"):
            validate_component_response(
                {"component_id": "minah_ai"}, expected_component_id="minah_ai",
                expected_operation_id="CASE-1:MINAH:REVIEW:1",
            )

    def test_nonaccepted_response_requires_failure_code_and_cannot_be_consumed_as_success(self):
        response = self.make_response(
            status="DEFERRED", data=None, failure_code="SOURCE_UNAVAILABLE"
        )
        checked = validate_component_response(
            response, expected_component_id="minah_ai",
            expected_operation_id="CASE-1:MINAH:REVIEW:1",
        )
        with self.assertRaisesRegex(ContractViolation, "SOURCE_UNAVAILABLE"):
            accepted_data(checked)
        with self.assertRaisesRegex(ContractViolation, "NON_ACCEPTED_RESPONSE_REQUIRES_FAILURE_CODE"):
            self.make_response(status="ERROR", data=None)

    def test_response_payload_is_detached_from_caller_mutation(self):
        original_data = {"status": "ADVISORY_ONLY"}
        response = self.make_response(data=original_data)
        original_data["status"] = "MUTATED"
        self.assertEqual(response["data"]["status"], "ADVISORY_ONLY")


if __name__ == "__main__":
    unittest.main()
