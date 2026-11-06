# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import os
import tempfile
import unittest

from institutional_authority import SQLiteNonceGuard


class SQLiteNonceGuardTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        self.now = [100]
        self.guard = SQLiteNonceGuard(self.path, clock=lambda: self.now[0])
        self.addCleanup(self.guard.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))

    def test_nonce_is_single_use_and_persisted_across_guard_instances(self):
        self.assertTrue(self.guard.consume("tenant-a", "nonce-unique-000001", 150))
        second = SQLiteNonceGuard(self.path, clock=lambda: self.now[0])
        self.addCleanup(second.close)
        self.assertFalse(second.consume("tenant-a", "nonce-unique-000001", 150))

    def test_nonce_scope_is_tenant_specific(self):
        self.assertTrue(self.guard.consume("tenant-a", "nonce-tenant-scope-01", 150))
        self.assertTrue(self.guard.consume("tenant-b", "nonce-tenant-scope-01", 150))

    def test_expired_nonce_records_are_pruned_and_nonce_can_be_reused(self):
        self.assertTrue(self.guard.consume("tenant-a", "nonce-expiring-000001", 110))
        self.now[0] = 110
        self.assertTrue(self.guard.consume("tenant-a", "nonce-prune-trigger-01", 130))
        self.assertTrue(self.guard.consume("tenant-a", "nonce-expiring-000001", 130))

    def test_expired_or_malformed_nonce_is_rejected(self):
        self.assertFalse(self.guard.consume("tenant-a", "nonce-expired-000001", 99))
        self.assertFalse(self.guard.consume("", "nonce-malformed-000001", 150))
        self.assertFalse(self.guard.consume("tenant-a", "", 150))

    def test_missing_expiry_uses_short_assertion_default(self):
        self.assertTrue(self.guard.consume("tenant-a", "nonce-default-ttl-001"))
        self.now[0] = 400
        self.assertTrue(self.guard.consume("tenant-a", "nonce-default-ttl-001", 450))


if __name__ == "__main__":
    unittest.main()
