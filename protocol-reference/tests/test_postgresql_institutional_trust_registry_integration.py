# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL integration tests for persistent institutional trust and authority."""
from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DSN = os.environ.get("KIN_SYS_POSTGRES_TEST_DSN")

from postgresql_migrations import apply_migrations
from postgresql_institutional_trust_registry import PostgreSQLInstitutionalTrustRegistry
from institutional_authority import (
    AuthorityResolutionError, InMemoryNonceGuard, InstitutionalAuthorityResolver,
    sign_actor_claims, sign_federated_actor_claims,
)
from institutional_grant_provenance import sign_authority_grant
from institutional_trust_registry import TrustRegistryConflict, TrustRegistryError

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


@unittest.skipUnless(DSN, "KIN_SYS_POSTGRES_TEST_DSN is not configured")
class PostgreSQLInstitutionalTrustRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.psycopg, cls.sql = psycopg, sql

    def setUp(self):
        self.schema = "kin_sys_trust_" + uuid.uuid4().hex
        self.admin = self.psycopg.connect(DSN, autocommit=True)
        self.admin.execute(
            self.sql.SQL("CREATE SCHEMA {}").format(self.sql.Identifier(self.schema))
        )

        def connect():
            connection = self.psycopg.connect(DSN)
            connection.execute(
                self.sql.SQL("SET search_path TO {}").format(
                    self.sql.Identifier(self.schema)
                )
            )
            connection.commit()
            return connection

        self.connect = connect
        with self.connect() as connection:
            apply_migrations(connection)
        self.registry = PostgreSQLInstitutionalTrustRegistry(self.connect)
        self.now = 150
        self.hmac_key = b"local-reference-key-material-32-bytes"
        self.issuer_private = Ed25519PrivateKey.generate()
        self.issuer_public = self.issuer_private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.issuer_private_2 = Ed25519PrivateKey.generate()
        self.issuer_public_2 = self.issuer_private_2.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.receiver_private = Ed25519PrivateKey.generate()
        self.receiver_public = self.receiver_private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        self.receiver_key = {
            "tenant_id": "tenant-a",
            "receiver_institution_id": "BANK-UG-01",
            "key_id": "receiver-key-1",
            "algorithm": "Ed25519",
            "status": "ACTIVE",
            "public_key_ref": "vault://receiver/public-1",
            "valid_from": 100,
            "valid_until": 300,
        }
        self.approval = {
            "actor_ref": "actor:bank-approver",
            "approval_ref": "approval:receiver-1",
        }

    def tearDown(self):
        self.admin.execute(
            self.sql.SQL("DROP SCHEMA {} CASCADE").format(self.sql.Identifier(self.schema))
        )
        self.admin.close()

    def grant(self, *, grant_id="GRANT-01", version="1", status="ACTIVE",
              valid_from=100, valid_until=300, roles=None):
        return {
            "grant_id": grant_id, "tenant_id": "tenant-a", "status": status,
            "actor_ref": "actor:registrar", "institution_id": "BANK-UG-01",
            "authority_ref": "authority:registry",
            "roles": roles or ["reviewer"],
            "resource_types": ["institution"],
            "resource_ids": ["BANK-UG-01"],
            "valid_from": valid_from, "valid_until": valid_until,
            "version": version, "issuer_institution_id": "NATIONAL-REGISTRY",
            "approval_ref": "approval:issuer-1",
        }

    def create_local_grant(self, **kwargs):
        grant = self.grant(**kwargs)
        self.registry.create_grant_version(
            tenant_id="tenant-a", grant=grant, event_id="grant-created-" + grant["version"],
            actor_ref="actor:local-approver", evidence_ref="evidence:local-approval",
        )
        return grant

    def local_assertion(self, *, grant_version="1", nonce="nonce-local-00000001",
                        issued_at=140, expires_at=160, actor_ref="actor:registrar"):
        claims = {
            "key_id": "local-key", "tenant_id": "tenant-a",
            "actor_ref": actor_ref, "grant_id": "GRANT-01",
            "grant_version": grant_version, "issued_at": issued_at,
            "expires_at": expires_at, "nonce": nonce,
        }
        return {"claims": claims, "signature": sign_actor_claims(claims, self.hmac_key)}

    def local_resolver(self, *, clock=None):
        return InstitutionalAuthorityResolver(
            grant_store=self.registry, consume_nonce=InMemoryNonceGuard().consume,
            verification_keys={"local-key": self.hmac_key},
            clock=clock or (lambda: self.now),
        )

    def configure_issuer(self, *, key_id="issuer-key-1"):
        self.registry.register_trusted_issuer(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            trust_basis_ref="trust-agreement:01", valid_from=100, valid_until=500,
            actor_ref="actor:trust-admin", evidence_ref="evidence:trust-root",
        )
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id=key_id, algorithm="Ed25519",
            public_key_ref="vault://issuer/public-1", valid_from=100,
            valid_until=500, actor_ref="actor:trust-admin",
            evidence_ref="evidence:issuer-key",
        )

    def accept_federated_grant_revision(
        self, *, expected_version="1", version="2",
        event_id="grant-accepted-2", approval_ref="approval:receiver-2",
    ):
        pending = self.grant(
            version=version, status="PENDING",
            roles=["reviewer", "approver"],
        )
        envelope = sign_authority_grant(
            pending, key_id="issuer-key-2", private_key=self.issuer_private_2,
        )
        approval = {
            "actor_ref": "actor:bank-approver",
            "approval_ref": approval_ref,
        }
        result = self.registry.accept_signed_grant_revision(
            tenant_id="tenant-a", grant_id="GRANT-01",
            expected_version=expected_version, envelope=envelope,
            receiver_institution_id="BANK-UG-01",
            receiver_approval=approval,
            verify_receiver_approval=lambda grant, value: value == approval,
            public_key_loader=lambda ref: (
                self.issuer_public_2 if ref == "vault://issuer/public-2" else None
            ),
            event_id=event_id, actor_ref="actor:bank-approver",
            evidence_ref=approval_ref,
            receiver_signing_key_id="receiver-key-1",
            receiver_signing_key_loader=lambda tenant, institution, key: (
                self.receiver_private if (tenant, institution, key) ==
                ("tenant-a", "BANK-UG-01", "receiver-key-1") else None
            ),
            clock=lambda: self.now,
        )
        return envelope, result

    def accept_federated_grant(self):
        pending = self.grant(status="PENDING")
        envelope = sign_authority_grant(
            pending, key_id="issuer-key-2", private_key=self.issuer_private_2,
        )
        result = self.registry.accept_signed_grant(
            tenant_id="tenant-a", envelope=envelope,
            receiver_institution_id="BANK-UG-01",
            receiver_approval=self.approval,
            verify_receiver_approval=lambda grant, approval: approval == self.approval,
            public_key_loader=lambda ref: (
                self.issuer_public_2 if ref == "vault://issuer/public-2" else None
            ),
            event_id="grant-accepted-1", actor_ref="actor:bank-approver",
            evidence_ref="approval:receiver-1",
            receiver_signing_key_id="receiver-key-1",
            receiver_signing_key_loader=lambda tenant, institution, key: (
                self.receiver_private if (tenant, institution, key) ==
                ("tenant-a", "BANK-UG-01", "receiver-key-1") else None
            ),
            clock=lambda: self.now,
        )
        return envelope, result

    def receiver_key_loader(self, tenant, institution, key):
        if (tenant, institution, key) == ("tenant-a", "BANK-UG-01", "receiver-key-1"):
            return dict(self.receiver_key)
        return None

    def receiver_public_key_loader(self, reference):
        return self.receiver_public if reference == "vault://receiver/public-1" else None

    def test_tenant_scoped_persistence_and_local_resolver_integration(self):
        self.create_local_grant()
        self.assertIsNone(self.registry.load_grant("tenant-b", "GRANT-01"))
        resolver = self.local_resolver()
        resolved = resolver.resolve(
            self.local_assertion(), tenant_id="tenant-a",
            resource_type="institution", resource_id="BANK-UG-01",
        )
        self.assertEqual(resolved["grant_ref"], "GRANT-01@1")
        self.assertEqual(resolved["roles"], ["reviewer"])
        self.assertIsNone(self.registry.load_grant("tenant-b", "GRANT-01"))

    def test_database_row_level_security_fails_closed_and_isolates_tenants(self):
        # Exercise the policies as a non-owner, non-BYPASSRLS database role.
        self.create_local_grant()
        tenant_b = dict(
            self.grant(),
            tenant_id="tenant-b",
            actor_ref="actor:registrar-b",
            institution_id="BANK-KE-01",
            authority_ref="authority:ke-registry",
            resource_ids=["BANK-KE-01"],
            approval_ref="approval:ke-01",
        )
        self.registry.create_grant_version(
            tenant_id="tenant-b",
            grant=tenant_b,
            event_id="tenant-b-grant-created-rls",
            actor_ref="actor:ke-approver",
            evidence_ref="evidence:ke-approval",
        )

        role_name = "kin_sys_rls_" + uuid.uuid4().hex[:16]
        role = self.sql.Identifier(role_name)
        self.admin.execute(self.sql.SQL("CREATE ROLE {} NOLOGIN").format(role))
        self.admin.execute(
            self.sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                self.sql.Identifier(self.schema), role,
            )
        )
        self.admin.execute(
            self.sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}"
            ).format(self.sql.Identifier(self.schema), role)
        )

        connection = self.connect()
        try:
            connection.execute(self.sql.SQL("SET ROLE {}").format(role))
            connection.execute(
                "SELECT set_config('kin_sys.tenant_id', %s, true)", ("tenant-a",),
            )
            rows = connection.execute(
                "SELECT tenant_id FROM authority_grant_versions WHERE grant_id='GRANT-01'"
            ).fetchall()
            self.assertEqual(rows, [("tenant-a",)])
            connection.commit()

            # SET LOCAL context must not leak when this connection is reused.
            rows_without_context = connection.execute(
                "SELECT tenant_id FROM authority_grant_versions WHERE grant_id='GRANT-01'"
            ).fetchall()
            self.assertEqual(rows_without_context, [])
            connection.rollback()

            connection.execute(
                "SELECT set_config('kin_sys.tenant_id', %s, true)", ("tenant-a",),
            )
            update = connection.execute(
                """UPDATE authority_grant_versions SET status='REVOKED'
                   WHERE tenant_id='tenant-b' AND grant_id='GRANT-01'"""
            )
            self.assertEqual(update.rowcount, 0)

            # WITH CHECK must also prevent a visible tenant-A row from being
            # reassigned into tenant B, even when the caller can issue UPDATE.
            with self.assertRaises(self.psycopg.Error):
                connection.execute(
                    """UPDATE authority_grant_versions SET tenant_id='tenant-b'
                       WHERE tenant_id='tenant-a' AND grant_id='GRANT-01'"""
                )
            connection.rollback()

            # A tenant-A delete targeting tenant B must affect no rows.
            connection.execute(
                "SELECT set_config('kin_sys.tenant_id', %s, true)", ("tenant-a",),
            )
            delete = connection.execute(
                """DELETE FROM authority_grant_versions
                   WHERE tenant_id='tenant-b' AND grant_id='GRANT-01'"""
            )
            self.assertEqual(delete.rowcount, 0)
            connection.commit()

            connection.execute(
                "SELECT set_config('kin_sys.tenant_id', %s, true)", ("tenant-b",),
            )
            rows_tenant_b = connection.execute(
                "SELECT tenant_id FROM authority_grant_versions WHERE grant_id='GRANT-01'"
            ).fetchall()
            self.assertEqual(rows_tenant_b, [("tenant-b",)])
            connection.commit()
        finally:
            connection.close()
            self.admin.execute(
                self.sql.SQL("DROP OWNED BY {}").format(role)
            )
            self.admin.execute(
                self.sql.SQL("DROP ROLE {}").format(role)
            )

    def test_revocation_isolated_between_tenants_with_same_grant_id(self):
        tenant_a = self.create_local_grant()
        tenant_b = dict(
            tenant_a,
            tenant_id="tenant-b",
            actor_ref="actor:registrar-b",
            institution_id="BANK-KE-01",
            resource_ids=["BANK-KE-01"],
            authority_ref="authority:ke-registry",
            approval_ref="approval:ke-issuer",
        )
        self.registry.create_grant_version(
            tenant_id="tenant-b",
            grant=tenant_b,
            event_id="tenant-b-grant-created-1",
            actor_ref="actor:ke-approver",
            evidence_ref="evidence:ke-approval",
        )

        resolver_b = InstitutionalAuthorityResolver(
            grant_store=self.registry,
            consume_nonce=InMemoryNonceGuard().consume,
            verification_keys={"local-key-b": self.hmac_key},
            clock=lambda: self.now,
        )

        def resolve_b(nonce):
            claims = {
                "key_id": "local-key-b",
                "tenant_id": "tenant-b",
                "actor_ref": "actor:registrar-b",
                "grant_id": "GRANT-01",
                "grant_version": "1",
                "issued_at": 140,
                "expires_at": 160,
                "nonce": nonce,
            }
            return resolver_b.resolve(
                {"claims": claims, "signature": sign_actor_claims(claims, self.hmac_key)},
                tenant_id="tenant-b",
                resource_type="institution",
                resource_id="BANK-KE-01",
            )

        self.assertEqual(resolve_b("tenant-b-before-revoke-0001")["grant_ref"], "GRANT-01@1")
        self.registry.revoke_grant(
            tenant_id="tenant-a",
            grant_id="GRANT-01",
            expected_version="1",
            new_version="2",
            event_id="tenant-a-grant-revoked-2",
            actor_ref="actor:ug-authority-admin",
            evidence_ref="evidence:ug-revocation",
        )

        self.assertEqual(
            self.registry.load_grant("tenant-a", "GRANT-01")["status"], "REVOKED"
        )
        self.assertEqual(self.registry.load_grant("tenant-a", "GRANT-01")["version"], "2")
        self.assertEqual(self.registry.load_grant("tenant-b", "GRANT-01"), tenant_b)
        self.assertEqual(resolve_b("tenant-b-after-revoke-0002")["grant_ref"], "GRANT-01@1")
        self.assertEqual(
            [event["event_type"] for event in self.registry.list_grant_events(
                tenant_id="tenant-b", grant_id="GRANT-01")],
            ["GRANT_VERSION_CREATED"],
        )

    def test_immutable_versions_revision_head_and_audited_revocation(self):
        self.create_local_grant()
        revised = self.grant(version="2", roles=["reviewer", "approver"])
        self.registry.create_grant_revision(
            tenant_id="tenant-a", grant_id="GRANT-01", expected_version="1",
            grant=revised, event_id="grant-revised-2",
            actor_ref="actor:local-approver", evidence_ref="evidence:grant-revision",
        )
        self.assertEqual(self.registry.load_grant("tenant-a", "GRANT-01")["version"], "2")
        with self.assertRaisesRegex(TrustRegistryConflict, "STALE_GRANT_VERSION"):
            self.registry.create_grant_revision(
                tenant_id="tenant-a", grant_id="GRANT-01", expected_version="1",
                grant=self.grant(version="3"), event_id="grant-stale-3",
                actor_ref="actor:local-approver", evidence_ref="evidence:stale",
            )
        with self.assertRaises(TrustRegistryConflict):
            self.registry.create_grant_version(
                tenant_id="tenant-a", grant=self.grant(),
                event_id="grant-duplicate", actor_ref="actor:local-approver",
                evidence_ref="evidence:duplicate",
            )
        self.registry.revoke_grant(
            tenant_id="tenant-a", grant_id="GRANT-01", expected_version="2",
            new_version="3", event_id="grant-revoked-3",
            actor_ref="actor:local-approver", evidence_ref="evidence:revocation",
        )
        self.assertEqual(self.registry.load_grant("tenant-a", "GRANT-01")["status"], "REVOKED")
        events = self.registry.list_grant_events(tenant_id="tenant-a", grant_id="GRANT-01")
        self.assertEqual([event["event_type"] for event in events], [
            "GRANT_VERSION_CREATED", "GRANT_AMENDED", "GRANT_REVOKED",
        ])
        with self.connect() as connection:
            with self.assertRaises(self.psycopg.Error):
                connection.execute(
                    """UPDATE authority_grant_versions SET status='ACTIVE'
                       WHERE tenant_id='tenant-a' AND grant_id='GRANT-01' AND version='1'"""
                )
            connection.rollback()
        with self.connect() as connection:
            connection.execute(
                """UPDATE authority_grant_heads SET current_version='1'
                   WHERE tenant_id='tenant-a' AND grant_id='GRANT-01'"""
            )
            with self.assertRaises(self.psycopg.Error):
                connection.commit()
            connection.rollback()

    def test_resolver_enforces_exact_version_actor_scope_and_expiry(self):
        self.create_local_grant()
        resolver = self.local_resolver()
        with self.assertRaisesRegex(AuthorityResolutionError, "AUTHORITY_RESOURCE_TYPE_OUT_OF_SCOPE"):
            resolver.resolve(
                self.local_assertion(), tenant_id="tenant-a",
                resource_type="payment", resource_id="BANK-UG-01",
            )
        with self.assertRaisesRegex(AuthorityResolutionError, "AUTHORITY_RESOURCE_ID_OUT_OF_SCOPE"):
            resolver.resolve(
                self.local_assertion(nonce="nonce-resource-id-0001"),
                tenant_id="tenant-a", resource_type="institution",
                resource_id="BANK-UG-02",
            )
        with self.assertRaisesRegex(AuthorityResolutionError, "ACTOR_NOT_BOUND_TO_AUTHORITY_GRANT"):
            resolver.resolve(
                self.local_assertion(actor_ref="actor:other-actor-0001"),
                tenant_id="tenant-a", resource_type="institution", resource_id="BANK-UG-01",
            )
        with self.assertRaisesRegex(AuthorityResolutionError, "AUTHORITY_GRANT_OUTSIDE_VALIDITY"):
            self.local_resolver(clock=lambda: 301).resolve(
                self.local_assertion(issued_at=290, expires_at=310, nonce="nonce-expiry-00000001"),
                tenant_id="tenant-a", resource_type="institution", resource_id="BANK-UG-01",
            )
        revised = self.grant(version="2")
        self.registry.create_grant_revision(
            tenant_id="tenant-a", grant_id="GRANT-01", expected_version="1",
            grant=revised, event_id="grant-revised-for-pin",
            actor_ref="actor:local-approver", evidence_ref="evidence:grant-revision",
        )
        with self.assertRaisesRegex(AuthorityResolutionError, "AUTHORITY_GRANT_SCOPE_MISMATCH"):
            resolver.resolve(
                self.local_assertion(nonce="nonce-old-version-0001"),
                tenant_id="tenant-a", resource_type="institution", resource_id="BANK-UG-01",
            )

    def test_federated_revision_requires_fresh_issuer_and_receiver_signatures(self):
        self.configure_issuer()
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="issuer-key-2", algorithm="Ed25519",
            public_key_ref="vault://issuer/public-2", valid_from=100,
            valid_until=500, actor_ref="actor:trust-admin",
            evidence_ref="evidence:issuer-key-rotation",
        )
        self.accept_federated_grant()
        envelope, revised = self.accept_federated_grant_revision()
        self.assertTrue(revised["accepted"])
        self.assertTrue(revised["revised"])
        self.assertEqual(revised["from_version"], "1")
        self.assertEqual(revised["grant"]["version"], "2")
        self.assertEqual(revised["grant"]["status"], "ACTIVE")

        head = self.registry.load_grant("tenant-a", "GRANT-01")
        self.assertEqual(head["version"], "2")
        self.assertEqual(head["roles"], ["reviewer", "approver"])
        self.assertIsNotNone(self.registry.load_grant_provenance(
            tenant_id="tenant-a", grant_id="GRANT-01", version="1"))
        provenance = self.registry.load_grant_provenance(
            tenant_id="tenant-a", grant_id="GRANT-01", version="2")
        self.assertEqual(provenance["issuer_envelope"], envelope)
        self.assertEqual(
            provenance["acceptance"]["receiver_receipt"],
            revised["receiver_receipt"],
        )

        verified = self.registry.load_verified_grant(
            "tenant-a", "GRANT-01", require_federated_provenance=True,
            public_key_loader=lambda ref: (
                self.issuer_public_2 if ref == "vault://issuer/public-2" else None
            ),
            receiver_acceptance_key_loader=self.receiver_key_loader,
            receiver_acceptance_public_key_loader=self.receiver_public_key_loader,
            clock=lambda: self.now,
        )
        self.assertEqual(verified["version"], "2")

        resolver = InstitutionalAuthorityResolver(
            grant_store=self.registry, consume_nonce=InMemoryNonceGuard().consume,
            trust_key_loader=self.registry.load_trust_key,
            public_key_loader=lambda ref: (
                self.issuer_public_2 if ref == "vault://issuer/public-2" else None
            ),
            receiver_acceptance_key_loader=self.receiver_key_loader,
            receiver_acceptance_public_key_loader=self.receiver_public_key_loader,
            clock=lambda: self.now,
        )
        claims = {
            "key_id": "issuer-key-2", "tenant_id": "tenant-a",
            "actor_ref": "actor:registrar", "grant_id": "GRANT-01",
            "grant_version": "2", "issued_at": 140, "expires_at": 160,
            "nonce": "nonce-federated-revision-2",
            "issuer_institution_id": "NATIONAL-REGISTRY",
        }
        assertion = {
            "claims": claims,
            "signature": sign_federated_actor_claims(claims, self.issuer_private_2),
        }
        resolved = resolver.resolve(
            assertion, tenant_id="tenant-a",
            resource_type="institution", resource_id="BANK-UG-01",
        )
        self.assertEqual(resolved["grant_ref"], "GRANT-01@2")

        old_claims = dict(claims, grant_version="1", nonce="nonce-federated-old-1")
        old_assertion = {
            "claims": old_claims,
            "signature": sign_federated_actor_claims(
                old_claims, self.issuer_private_2,
            ),
        }
        with self.assertRaisesRegex(
            AuthorityResolutionError, "AUTHORITY_GRANT_SCOPE_MISMATCH",
        ):
            resolver.resolve(
                old_assertion, tenant_id="tenant-a",
                resource_type="institution", resource_id="BANK-UG-01",
            )

    def test_federated_revision_stale_head_and_audit_conflict_roll_back(self):
        self.configure_issuer()
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="issuer-key-2", algorithm="Ed25519",
            public_key_ref="vault://issuer/public-2", valid_from=100,
            valid_until=500, actor_ref="actor:trust-admin",
            evidence_ref="evidence:issuer-key-rotation",
        )
        self.accept_federated_grant()

        with self.assertRaisesRegex(TrustRegistryConflict, "STALE_GRANT_VERSION"):
            self.accept_federated_grant_revision(
                expected_version="0", version="2", event_id="stale-revision",
            )
        self.assertEqual(
            self.registry.load_grant("tenant-a", "GRANT-01")["version"], "1",
        )
        self.assertIsNone(self.registry.load_grant_provenance(
            tenant_id="tenant-a", grant_id="GRANT-01", version="2"))

        with self.assertRaisesRegex(
            TrustRegistryConflict, "GRANT_EVENT_ID_ALREADY_EXISTS",
        ):
            self.accept_federated_grant_revision(
                expected_version="1", version="2", event_id="grant-accepted-1",
            )
        self.assertEqual(
            self.registry.load_grant("tenant-a", "GRANT-01")["version"], "1",
        )
        self.assertIsNone(self.registry.load_grant_provenance(
            tenant_id="tenant-a", grant_id="GRANT-01", version="2"))
        events = self.registry.list_grant_events(
            tenant_id="tenant-a", grant_id="GRANT-01",
        )
        self.assertEqual([event["version"] for event in events], ["1"])

    def test_federated_revision_refusal_does_not_resolve_receiver_signer(self):
        self.configure_issuer()
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="issuer-key-2", algorithm="Ed25519",
            public_key_ref="vault://issuer/public-2", valid_from=100,
            valid_until=500, actor_ref="actor:trust-admin",
            evidence_ref="evidence:issuer-key-rotation",
        )
        self.accept_federated_grant()
        pending = self.grant(version="2", status="PENDING")
        envelope = sign_authority_grant(
            pending, key_id="issuer-key-2", private_key=self.issuer_private_2,
        )
        signer_called = False

        def signer_loader(*args):
            nonlocal signer_called
            signer_called = True
            return self.receiver_private

        with self.assertRaisesRegex(
            TrustRegistryError, "SIGNED_GRANT_REVISION_ACCEPTANCE_REJECTED",
        ):
            self.registry.accept_signed_grant_revision(
                tenant_id="tenant-a", grant_id="GRANT-01",
                expected_version="1", envelope=envelope,
                receiver_institution_id="BANK-UG-01",
                receiver_approval=self.approval,
                verify_receiver_approval=lambda grant, approval: False,
                public_key_loader=lambda ref: (
                    self.issuer_public_2 if ref == "vault://issuer/public-2" else None
                ),
                event_id="refused-revision", actor_ref="actor:bank-approver",
                evidence_ref="approval:receiver-1",
                receiver_signing_key_id="receiver-key-1",
                receiver_signing_key_loader=signer_loader,
                clock=lambda: self.now,
            )
        self.assertFalse(signer_called)
        self.assertEqual(
            self.registry.load_grant("tenant-a", "GRANT-01")["version"], "1",
        )

    def test_unsigned_local_revision_cannot_inherit_federated_provenance(self):
        self.configure_issuer()
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="issuer-key-2", algorithm="Ed25519",
            public_key_ref="vault://issuer/public-2", valid_from=100,
            valid_until=500, actor_ref="actor:trust-admin",
            evidence_ref="evidence:issuer-key-rotation",
        )
        self.accept_federated_grant()
        self.registry.create_grant_revision(
            tenant_id="tenant-a", grant_id="GRANT-01", expected_version="1",
            grant=self.grant(version="2"), event_id="unsigned-federated-revision",
            actor_ref="actor:local-approver", evidence_ref="evidence:local-change",
        )
        with self.assertRaisesRegex(
            TrustRegistryError, "FEDERATED_GRANT_PROVENANCE_REQUIRED",
        ):
            self.registry.load_verified_grant(
                "tenant-a", "GRANT-01", require_federated_provenance=True,
                public_key_loader=lambda ref: (
                    self.issuer_public_2 if ref == "vault://issuer/public-2" else None
                ),
                receiver_acceptance_key_loader=self.receiver_key_loader,
                receiver_acceptance_public_key_loader=self.receiver_public_key_loader,
                clock=lambda: self.now,
            )

    def test_ed25519_issuer_trust_rotation_receiver_receipt_and_revocation(self):
        self.configure_issuer()
        self.registry.register_trust_key(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            key_id="issuer-key-2", algorithm="Ed25519",
            public_key_ref="vault://issuer/public-2", valid_from=100,
            valid_until=500, actor_ref="actor:trust-admin",
            evidence_ref="evidence:issuer-key-rotation",
        )
        self.assertEqual(self.registry.load_trust_key("tenant-a", "issuer-key-1")["status"], "ACTIVE")
        self.assertEqual(self.registry.load_trust_key("tenant-a", "issuer-key-2")["status"], "ACTIVE")
        with self.connect() as connection:
            connection.execute(
                """UPDATE institutional_trust_keys SET status='REVOKED'
                   WHERE tenant_id='tenant-a' AND key_id='issuer-key-2'"""
            )
            with self.assertRaises(self.psycopg.Error):
                connection.commit()
            connection.rollback()
        self.assertEqual(self.registry.load_trust_key("tenant-a", "issuer-key-2")["status"], "ACTIVE")
        self.registry.revoke_trust_key(
            tenant_id="tenant-a", key_id="issuer-key-1",
            actor_ref="actor:trust-admin", evidence_ref="evidence:key-revocation",
            event_id="issuer-key-revoked",
        )
        self.assertEqual(self.registry.load_trust_key("tenant-a", "issuer-key-1")["status"], "REVOKED")
        self.assertEqual(self.registry.list_trust_key_events(
            tenant_id="tenant-a", key_id="issuer-key-1")[-1]["event_type"], "TRUST_KEY_REVOKED")

        envelope, accepted = self.accept_federated_grant()
        self.assertTrue(accepted["accepted"])
        verified = self.registry.load_verified_grant(
            "tenant-a", "GRANT-01", require_federated_provenance=True,
            public_key_loader=lambda ref: (
                self.issuer_public_2 if ref == "vault://issuer/public-2" else None
            ),
            receiver_acceptance_key_loader=self.receiver_key_loader,
            receiver_acceptance_public_key_loader=self.receiver_public_key_loader,
            clock=lambda: self.now,
        )
        self.assertEqual(verified["status"], "ACTIVE")
        provenance = self.registry.load_grant_provenance(
            tenant_id="tenant-a", grant_id="GRANT-01", version="1")
        self.assertEqual(provenance["issuer_envelope"], envelope)

        claims = {
            "key_id": "issuer-key-1", "tenant_id": "tenant-a",
            "actor_ref": "actor:registrar", "grant_id": "GRANT-01",
            "grant_version": "1", "issued_at": 140, "expires_at": 160,
            "nonce": "nonce-federated-0001",
            "issuer_institution_id": "NATIONAL-REGISTRY",
        }
        # Use the overlapping, still-active second key for the assertion.
        claims["key_id"] = "issuer-key-2"
        resolver = InstitutionalAuthorityResolver(
            grant_store=self.registry, consume_nonce=InMemoryNonceGuard().consume,
            trust_key_loader=self.registry.load_trust_key,
            public_key_loader=lambda ref: (
                {"vault://issuer/public-1": self.issuer_public,
                 "vault://issuer/public-2": self.issuer_public_2}.get(ref)
            ),
            receiver_acceptance_key_loader=self.receiver_key_loader,
            receiver_acceptance_public_key_loader=self.receiver_public_key_loader,
            clock=lambda: self.now,
        )
        assertion = {
            "claims": claims,
            "signature": sign_federated_actor_claims(claims, self.issuer_private_2),
        }
        resolved = resolver.resolve(
            assertion, tenant_id="tenant-a",
            resource_type="institution", resource_id="BANK-UG-01",
        )
        self.assertEqual(resolved["grant_ref"], "GRANT-01@1")

        self.registry.revoke_trusted_issuer(
            tenant_id="tenant-a", issuer_institution_id="NATIONAL-REGISTRY",
            actor_ref="actor:trust-admin", evidence_ref="evidence:issuer-revocation",
            event_id="issuer-trust-revoked",
        )
        with self.assertRaisesRegex(TrustRegistryError, "GRANT_PROVENANCE_VERIFICATION_FAILED"):
            self.registry.load_verified_grant(
                "tenant-a", "GRANT-01", require_federated_provenance=True,
                public_key_loader=lambda ref: self.issuer_public,
                receiver_acceptance_key_loader=self.receiver_key_loader,
                receiver_acceptance_public_key_loader=self.receiver_public_key_loader,
                clock=lambda: self.now,
            )


if __name__ == "__main__":
    unittest.main()
