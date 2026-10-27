# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Tests for the fail-closed MINAH profile approval contract."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from minah_profile_governance import (
    APPROVAL_VERSION,
    MinahApprovalViolation,
    profile_digest,
    verify_minah_profile_approval,
)
from minah_deployment_profile import PROFILE_VERSION


def profile(**overrides):
    value = {
        "profile_version": PROFILE_VERSION,
        "deployment_id": "ug-national-minah-01",
        "owner_scope": "NATIONAL_SOVEREIGN",
        "owner_ref": "UG-INSTITUTION-ROOT",
        "use_case": "PUBLIC_REVENUE_CLEARANCE",
        "jurisdictions": ["UG"],
        "policy_pack_version": "UG-REVENUE-POLICY-3",
        "source_pack_version": "UG-LEGAL-SOURCES-2026-10",
        "model_ref": "institution-selected-model-v2",
        "deployment_mode": "ON_PREMISES",
        "may_authorise": False,
    }
    value.update(overrides)
    return value


def approval_for(value, **overrides):
    approval = {
        "approval_version": APPROVAL_VERSION,
        "approval_id": "approval-0001",
        "signer_ref": "trusted-owner-key-01",
        "algorithm": "TEST-ONLY",
        "profile_digest": profile_digest(value),
        "approved_at": "2026-10-26T10:00:00Z",
        "expires_at": "2027-10-26T10:00:00Z",
        "signature": "test-signature",
    }
    approval.update(overrides)
    return approval


class MinahProfileGovernanceTests(unittest.TestCase):
    NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)

    def verify(self, value=None, envelope=None, *, signature=True, revoked=False,
               signature_error=False, revocation_error=False):
        value = value or profile()
        envelope = envelope or approval_for(value)

        def verifier(signer_ref, algorithm, payload, signature_value):
            if signature_error:
                raise TimeoutError("key registry unavailable")
            return signature and signer_ref == "trusted-owner-key-01" and bool(payload)

        def revocation_checker(approval_id):
            if revocation_error:
                raise TimeoutError("revocation registry unavailable")
            return revoked

        return verify_minah_profile_approval(
            value, envelope, signature_verifier=verifier,
            revocation_checker=revocation_checker, now=self.NOW,
        )

    def test_accepts_approval_when_signature_and_revocation_checks_pass(self):
        result = self.verify()
        self.assertTrue(result["verified"])
        self.assertEqual(result["status"], "APPROVED")
        self.assertTrue(result["revocation_checked"])

    def test_modified_profile_invalidates_approval_digest(self):
        original = profile()
        changed = profile(policy_pack_version="UNAPPROVED-POLICY")
        original_approval = approval_for(original)
        with self.assertRaisesRegex(MinahApprovalViolation, "PROFILE_DIGEST_MISMATCH"):
            self.verify(value=changed, envelope=original_approval)

    def test_invalid_signature_is_rejected(self):
        with self.assertRaisesRegex(MinahApprovalViolation, "SIGNATURE_INVALID_OR_UNTRUSTED"):
            self.verify(signature=False)

    def test_revoked_approval_is_rejected(self):
        with self.assertRaisesRegex(MinahApprovalViolation, "APPROVAL_REVOKED"):
            self.verify(revoked=True)

    def test_revocation_registry_outage_fails_closed(self):
        with self.assertRaisesRegex(MinahApprovalViolation, "REVOCATION_CHECK_UNAVAILABLE"):
            self.verify(revocation_error=True)

    def test_signature_verification_outage_fails_closed(self):
        with self.assertRaisesRegex(MinahApprovalViolation, "SIGNATURE_VERIFICATION_UNAVAILABLE"):
            self.verify(signature_error=True)

    def test_expired_approval_is_rejected(self):
        envelope = approval_for(profile(), expires_at="2026-10-27T11:59:59Z")
        with self.assertRaisesRegex(MinahApprovalViolation, "APPROVAL_EXPIRED"):
            self.verify(envelope=envelope)

    def test_timezone_is_required(self):
        envelope = approval_for(profile(), approved_at="2026-10-26T10:00:00")
        with self.assertRaisesRegex(MinahApprovalViolation, "TIMEZONE"):
            self.verify(envelope=envelope)

    def test_signature_covers_approval_metadata(self):
        envelope = approval_for(profile(), signer_ref="untrusted-owner")
        with self.assertRaisesRegex(MinahApprovalViolation, "SIGNATURE_INVALID_OR_UNTRUSTED"):
            self.verify(envelope=envelope)

    def test_unknown_revocation_state_does_not_pass(self):
        value = profile()
        envelope = approval_for(value)
        def unknown_revocation(approval_id):
            return None
        with self.assertRaisesRegex(MinahApprovalViolation, "REVOCATION_UNKNOWN"):
            verify_minah_profile_approval(
                value, envelope, signature_verifier=lambda *args: True,
                revocation_checker=unknown_revocation, now=self.NOW,
            )


if __name__ == "__main__":
    unittest.main()
