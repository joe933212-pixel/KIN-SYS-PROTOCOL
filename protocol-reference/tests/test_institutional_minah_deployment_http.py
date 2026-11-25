# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""HTTP tests for authenticated institutional MINAH deployment onboarding."""
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
from institutional_identity_provider_registry import InstitutionalIdentityProviderRegistry
from institutional_minah_deployment import (
    InstitutionalMinahDeploymentAdministration,
    InstitutionalMinahDeploymentRegistry,
)


class Authority:
    def __init__(self):
        self.roles = ["minah_deployment_registrar", "minah_deployment_configurator",
                      "minah_deployment_approver", "minah_deployment_suspension_authority",
                      "minah_deployment_revocation_authority"]
        self.seen = set()

    def resolve(self, assertion, *, tenant_id, resource_type, resource_id):
        nonce = assertion.get("nonce")
        if nonce in self.seen:
            raise ValueError("replayed assertion")
        self.seen.add(nonce)
        return {"authenticated": True, "actor_ref": "actor:institutional-admin",
                "roles": list(self.roles)}


class Sessions:
    def __init__(self):
        self.tenant_id = "tenant-ug-gov"
        self.nonce = 0

    def resolve(self, environ):
        self.nonce += 1
        return {"authenticated": True, "tenant_id": self.tenant_id,
                "actor_assertion": {"nonce": "http-session-nonce-" + str(self.nonce)}}


def config():
    return {
        "deployment_id": "minah-ug-001", "version": "1", "status": "PENDING",
        "agent_kind": "INTERNAL", "agent_ref": "agent:gov-minah",
        "interface_version": "minah-review-0.1", "deployment_mode": "ON_PREMISES",
        "model_ref": "model:gov/minah-v1", "endpoint_ref": None,
        "secret_ref": "secret://gov/minah/credential",
        "owner_scope": "NATIONAL_SOVEREIGN", "jurisdictions": ["UG"],
        "identity_provider_id": "idp-ug", "identity_provider_version": "2",
        "jurisdiction_package_id": "UG-JURISDICTION", "jurisdiction_package_version": "4",
        "mandate_ref": "mandate:gov-1", "mandate_version": "1",
        "permitted_operations": ["trade.review"],
        "prohibited_operations": ["authority.grant", "bypass.cee"],
        "decision_boundaries": ["CEE_FINAL_ADMISSION"],
        "policy_pack_version": "policy-1", "source_pack_version": "sources-1",
        "authority_profile_version": "authority-1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


class MinahDeploymentHttpTests(unittest.TestCase):
    def setUp(self):
        fd, self.registry_db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        fd, self.provider_db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.registry = InstitutionalMinahDeploymentRegistry(self.registry_db)
        self.providers = InstitutionalIdentityProviderRegistry(self.provider_db)
        now = int(time.time())
        provider = {
            "tenant_id": "tenant-ug-gov", "provider_id": "idp-ug", "version": "2",
            "protocol": "OIDC", "issuer": "https://idp.example.ug",
            "client_id": "kin-sys", "status": "ACTIVE",
            "allowed_hosts": ["idp.example.ug"], "allowed_algorithms": ["RS256"],
            "audiences": ["kin-sys"], "allowed_acr": ["loa-2"], "required_amr": [],
            "valid_from": now - 60, "valid_until": now + 3600, "cache_ttl_seconds": 60,
        }
        self.providers.register_provider(tenant_id="tenant-ug-gov", config={**provider, "status": "PENDING"},
            event_id="idp-register", actor_ref="actor:idp-admin", evidence_ref="evidence:idp")
        self.providers.transition_provider(tenant_id="tenant-ug-gov", provider_id="idp-ug",
            expected_version="2", new_version="3", new_status="ACTIVE",
            event_id="idp-activate", actor_ref="actor:idp-approver", evidence_ref="evidence:idp-approval")
        # The deployment pins the current active provider version.
        self.providers.transition_provider(tenant_id="tenant-ug-gov", provider_id="idp-ug",
            expected_version="3", new_version="4", new_status="SUSPENDED",
            event_id="idp-suspend", actor_ref="actor:idp-approver", evidence_ref="evidence:idp-suspend")
        # Restore a separate active provider configuration at version 5 for this test.
        self.providers.transition_provider(tenant_id="tenant-ug-gov", provider_id="idp-ug",
            expected_version="4", new_version="5", new_status="ACTIVE",
            event_id="idp-reactivate", actor_ref="actor:idp-approver", evidence_ref="evidence:idp-reactivate")
        self.providers_config_version = "5"
        self.authority = Authority()
        self.sessions = Sessions()
        self.admin = InstitutionalMinahDeploymentAdministration(
            registry=self.registry, authority_resolver=self.authority,
            identity_provider_registry=self.providers)
        self.app = create_app(minah_deployment_administration=self.admin,
            institutional_session_resolver=self.sessions)
        self.addCleanup(self.app.close)
        self.addCleanup(self.registry.close)
        self.addCleanup(self.providers.close)
        self.addCleanup(lambda: os.path.exists(self.registry_db) and os.unlink(self.registry_db))
        self.addCleanup(lambda: os.path.exists(self.provider_db) and os.unlink(self.provider_db))

    def request(self, path, method="GET", body=None):
        raw = b"" if body is None else json.dumps(body).encode()
        environ = {"REQUEST_METHOD": method, "PATH_INFO": path,
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(raw)),
            "REMOTE_ADDR": "127.0.0.1", "wsgi.input": io.BytesIO(raw)}
        captured = {}
        def start_response(status, headers):
            captured["status"] = int(status.split()[0])
        payload = json.loads(b"".join(self.app(environ, start_response)).decode())
        return captured["status"], payload

    def test_register_get_and_audit_use_session_tenant(self):
        body = config()
        body["identity_provider_version"] = self.providers_config_version
        status, result = self.request("/institutional/minah-deployments", "POST",
            {"config": body, "event_id": "minah-register", "evidence_ref": "evidence:mandate"})
        self.assertEqual(status, 201)
        self.assertEqual(result["deployment"]["status"], "PENDING")
        status, result = self.request("/institutional/minah-deployments/minah-ug-001")
        self.assertEqual(status, 200)
        self.assertEqual(result["deployment"]["tenant_id"], "tenant-ug-gov")
        status, result = self.request("/institutional/minah-deployments/minah-ug-001/events")
        self.assertEqual(status, 200)
        self.assertEqual(result["events"][0]["actor_ref"], "actor:institutional-admin")

    def test_request_cannot_choose_tenant_or_owner(self):
        body = config()
        body["tenant_id"] = "tenant-other"
        status, _ = self.request("/institutional/minah-deployments", "POST",
            {"config": body, "event_id": "spoof", "evidence_ref": "evidence"})
        self.assertEqual(status, 422)
        self.assertIsNone(self.registry.get(tenant_id="tenant-ug-gov", deployment_id="minah-ug-001"))

    def test_wrong_provider_version_is_rejected(self):
        body = config()
        body["identity_provider_version"] = "2"
        status, result = self.request("/institutional/minah-deployments", "POST",
            {"config": body, "event_id": "wrong-idp-version", "evidence_ref": "evidence"})
        self.assertEqual(status, 422)
        self.assertEqual(result["error"]["code"], "MINAH_IDENTITY_PROVIDER_NOT_ACTIVE_OR_VERSION_MISMATCH")

    def test_unconfigured_service_returns_503(self):
        app = create_app()
        self.addCleanup(app.close)
        raw = b"{}"
        env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/institutional/minah-deployments",
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": "2",
            "REMOTE_ADDR": "127.0.0.1", "wsgi.input": io.BytesIO(raw)}
        captured = {}
        def start(status, headers):
            captured["status"] = int(status.split()[0])
        json.loads(b"".join(app(env, start)).decode())
        self.assertEqual(captured["status"], 503)


if __name__ == "__main__":
    unittest.main()
