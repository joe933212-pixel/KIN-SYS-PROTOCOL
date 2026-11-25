# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""HTTP integration tests for authenticated institutional provider administration."""
import io
import json
import os
import tempfile
import time
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from http_service import create_app
from institutional_identity_provider_administration import InstitutionalIdentityProviderAdministration
from institutional_identity_provider_registry import InstitutionalIdentityProviderRegistry


class AuthorityResolver:
    def __init__(self):
        self.roles = ["identity_provider_registrar", "identity_provider_approver",
                      "identity_provider_configurator", "identity_provider_revocation_authority",
                      "identity_provider_suspension_authority"]
        self.seen = set()
        self.reject_replay = False
        self.calls = []

    def resolve(self, assertion, *, tenant_id, resource_type, resource_id):
        self.calls.append((tenant_id, resource_type, resource_id))
        nonce = assertion.get("nonce")
        if self.reject_replay and nonce in self.seen:
            raise ValueError("replayed assertion")
        self.seen.add(nonce)
        return {"authenticated": True, "actor_ref": "actor:verified-admin",
                "roles": list(self.roles)}


class SessionResolver:
    def __init__(self, tenant_id="tenant-bank-ug", assertion=None, authenticated=True):
        self.tenant_id = tenant_id
        self.assertion = assertion or {"nonce": "session-assertion-nonce-0001"}
        self.authenticated = authenticated

    def resolve(self, environ):
        return {"authenticated": self.authenticated, "tenant_id": self.tenant_id,
                "actor_assertion": self.assertion}


class InstitutionalProviderHttpTests(unittest.TestCase):
    def setUp(self):
        fd, self.database = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.registry = InstitutionalIdentityProviderRegistry(self.database)
        self.authority = AuthorityResolver()
        self.admin = InstitutionalIdentityProviderAdministration(
            registry=self.registry, authority_resolver=self.authority)
        self.sessions = SessionResolver()
        self.app = create_app(
            provider_administration=self.admin,
            institutional_session_resolver=self.sessions,
        )
        self.addCleanup(self.app.close)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.database) and os.unlink(self.database))
        now = int(time.time())
        self.config = {
            "provider_id": "national-idp", "version": "1", "protocol": "OIDC",
            "issuer": "https://identity.example.ug/tenant-a",
            "client_id": "kin-sys-api", "status": "PENDING",
            "allowed_hosts": ["identity.example.ug", "keys.example.ug"],
            "allowed_algorithms": ["RS256"], "audiences": ["kin-sys-api"],
            "allowed_acr": ["urn:loa:2"], "required_amr": ["otp"],
            "valid_from": now - 60, "valid_until": now + 3600,
            "cache_ttl_seconds": 120,
        }

    def request(self, app, path, method="GET", body=None):
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        environ = {
            "REQUEST_METHOD": method, "PATH_INFO": path,
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(raw)),
            "REMOTE_ADDR": "127.0.0.1", "wsgi.input": io.BytesIO(raw),
        }
        captured = {}
        def start_response(status, headers):
            captured["status"] = int(status.split()[0])
            captured["headers"] = dict(headers)
        payload = json.loads(b"".join(app(environ, start_response)).decode("utf-8"))
        return captured, payload

    def register(self, app=None, config=None, event_id="register-event-1"):
        return self.request(app or self.app, "/institutional/identity-providers", "POST", {
            "config": config or self.config, "event_id": event_id,
            "evidence_ref": "approval-evidence-1",
        })

    def test_register_derives_tenant_from_verified_session_and_records_audit(self):
        response, body = self.register()
        self.assertEqual(response["status"], 201)
        stored = self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp")
        self.assertEqual(stored["tenant_id"], "tenant-bank-ug")
        self.assertEqual(stored["status"], "PENDING")
        event = self.registry.list_events(
            tenant_id="tenant-bank-ug", provider_id="national-idp")[0]
        self.assertEqual(event["actor_ref"], "actor:verified-admin")
        self.assertEqual(event["evidence_ref"], "approval-evidence-1")
        self.assertEqual(self.authority.calls[0],
                         ("tenant-bank-ug", "identity_provider", "national-idp"))

    def test_request_cannot_supply_or_spoof_tenant_context(self):
        response, body = self.register(config={**self.config, "tenant_id": "tenant-other"})
        self.assertEqual(response["status"], 422)
        self.assertEqual(body["error"]["code"], "PROVIDER_CONFIG_INVALID")
        self.assertIsNone(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp"))
        self.assertEqual(self.authority.calls, [])

    def test_missing_or_unauthenticated_session_fails_before_registry_mutation(self):
        app = create_app(provider_administration=self.admin,
                         institutional_session_resolver=SessionResolver(authenticated=False))
        self.addCleanup(app.close)
        response, body = self.register(app=app)
        self.assertEqual(response["status"], 401)
        self.assertEqual(body["error"]["code"], "INSTITUTIONAL_SESSION_INVALID")
        self.assertIsNone(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp"))

    def test_route_authorizer_must_approve_provider_administration_scope(self):
        calls = []
        class RouteAuthorizer:
            def authorize(self, environ, scope, resource_id):
                calls.append((scope, resource_id))
                return True
        app = create_app(
            provider_administration=self.admin,
            institutional_session_resolver=self.sessions,
            authorizer=RouteAuthorizer(),
            allow_unauthenticated_loopback=False,
        )
        self.addCleanup(app.close)
        response, _ = self.register(app=app)
        self.assertEqual(response["status"], 201)
        self.assertEqual(calls, [("identity-provider:register", None)])

    def test_session_resolver_exception_fails_closed_as_unauthenticated(self):
        class BrokenSessionResolver:
            def resolve(self, environ):
                raise RuntimeError("session backend unavailable")
        app = create_app(
            provider_administration=self.admin,
            institutional_session_resolver=BrokenSessionResolver(),
        )
        self.addCleanup(app.close)
        response, body = self.register(app=app)
        self.assertEqual(response["status"], 401)
        self.assertEqual(body["error"]["code"], "INSTITUTIONAL_SESSION_INVALID")
        self.assertEqual(self.authority.calls, [])

    def test_routes_return_service_unavailable_when_admin_boundary_is_not_wired(self):
        app = create_app()
        self.addCleanup(app.close)
        response, body = self.request(
            app, "/institutional/identity-providers", "POST", {})
        self.assertEqual(response["status"], 503)
        self.assertEqual(body["error"]["code"], "PROVIDER_ADMINISTRATION_NOT_CONFIGURED")

    def test_insufficient_role_returns_forbidden_without_side_effects(self):
        self.authority.roles = ["identity_provider_approver"]
        response, body = self.register()
        self.assertEqual(response["status"], 403)
        self.assertEqual(body["error"]["code"], "PROVIDER_ADMIN_ROLE_INSUFFICIENT")
        self.assertIsNone(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp"))
        self.assertEqual(self.registry.list_events(
            tenant_id="tenant-bank-ug", provider_id="national-idp"), [])

    def test_stale_configuration_version_is_conflict_and_does_not_mutate(self):
        response, _ = self.register()
        self.assertEqual(response["status"], 201)
        revised = {**self.config, "version": "2"}
        response, body = self.request(self.app,
            "/institutional/identity-providers/national-idp/configuration", "PUT", {
                "expected_version": "stale-version", "config": revised,
                "event_id": "revise-event-1", "evidence_ref": "revision-evidence-1",
            })
        self.assertEqual(response["status"], 409)
        self.assertEqual(body["error"]["code"], "STALE_PROVIDER_VERSION")
        self.assertEqual(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp")["version"], "1")
        self.assertEqual(len(self.registry.list_events(
            tenant_id="tenant-bank-ug", provider_id="national-idp")), 1)

    def test_replayed_authority_assertion_is_rejected_before_second_mutation(self):
        self.authority.reject_replay = True
        response, _ = self.register()
        self.assertEqual(response["status"], 201)
        other = {**self.config, "provider_id": "second-idp"}
        response, body = self.register(config=other, event_id="register-event-2")
        self.assertEqual(response["status"], 403)
        self.assertEqual(body["error"]["code"], "PROVIDER_ADMIN_AUTHORITY_REJECTED")
        self.assertIsNone(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="second-idp"))

    def test_provider_reads_and_event_reads_are_tenant_scoped(self):
        self.register()
        response, body = self.request(
            self.app, "/institutional/identity-providers/national-idp")
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["provider"]["tenant_id"], "tenant-bank-ug")
        response, body = self.request(
            self.app, "/institutional/identity-providers/national-idp/events")
        self.assertEqual(response["status"], 200)
        self.assertEqual(len(body["events"]), 1)
        self.sessions.tenant_id = "tenant-other"
        response, body = self.request(
            self.app, "/institutional/identity-providers/national-idp")
        self.assertEqual(response["status"], 404)

    def test_revocation_is_terminal_through_http_lifecycle_route(self):
        self.register()
        response, body = self.request(self.app,
            "/institutional/identity-providers/national-idp/lifecycle", "POST", {
                "expected_version": "1", "new_version": "2", "new_status": "REVOKED",
                "event_id": "revoke-event-1", "evidence_ref": "revocation-evidence-1",
            })
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["provider"]["status"], "REVOKED")
        response, body = self.request(self.app,
            "/institutional/identity-providers/national-idp/lifecycle", "POST", {
                "expected_version": "2", "new_version": "3", "new_status": "ACTIVE",
                "event_id": "reactivate-event-1", "evidence_ref": "approval-evidence-3",
            })
        self.assertEqual(response["status"], 422)
        self.assertEqual(body["error"]["code"], "PROVIDER_TRANSITION_NOT_ALLOWED")
        self.assertEqual(self.registry.get_provider(
            tenant_id="tenant-bank-ug", provider_id="national-idp")["version"], "2")


if __name__ == "__main__":
    unittest.main()
