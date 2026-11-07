# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from http_service import create_app
from institutional_authority import InMemoryNonceGuard, InstitutionalAuthorityResolver, sign_actor_claims
from institutional_onboarding_administration import InstitutionalOnboardingAdministration
from institutional_lifecycle_administration import InstitutionalLifecycleAdministration
from institutional_registry import InstitutionalRegistry


class InstitutionalDomainHttpTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        self.registry = InstitutionalRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))
        self.key = b"http-onboarding-test-key-at-least-32-bytes-long"
        self.grant = {
            "grant_id":"GRANT-HTTP","tenant_id":"tenant-http","status":"ACTIVE",
            "actor_ref":"actor:registrar","institution_id":"BANK-HTTP",
            "authority_ref":"authority:registry","roles":["institution_registrar","institution_verifier"],
            "resource_types":["institution"],"resource_ids":["BANK-HTTP"],
            "valid_from":900,"valid_until":1100,"version":"1",
            "issuer_institution_id":"REGISTRY-UG","approval_ref":"approval:grant",
        }
        self.verifier_grant = {
            **self.grant, "grant_id":"GRANT-VERIFIER", "actor_ref":"actor:verifier",
            "institution_id":"REGISTRY-UG", "roles":["institution_verifier"],
            "resource_types":["institution"], "resource_ids":["BANK-HTTP"],
        }
        self.nonces=InMemoryNonceGuard()
        self.resolver=InstitutionalAuthorityResolver(
            verification_keys={"key-http":self.key},
            grant_loader=lambda tenant, grant: (
                self.grant if tenant=="tenant-http" and grant=="GRANT-HTTP"
                else self.verifier_grant if tenant=="tenant-http" and grant=="GRANT-VERIFIER"
                else None),
            consume_nonce=self.nonces.consume,clock=lambda:1000)
        self.counter=0
        class Session:
            def __init__(inner, outer): inner.outer=outer
            def resolve(inner,environ):
                inner.outer.counter+=1
                lifecycle=environ.get("PATH_INFO","").endswith("/lifecycle")
                grant_id="GRANT-VERIFIER" if lifecycle else "GRANT-HTTP"
                actor_ref="actor:verifier" if lifecycle else "actor:registrar"
                claims={"key_id":"key-http","tenant_id":"tenant-http","actor_ref":actor_ref,
                    "grant_id":grant_id,"grant_version":"1","issued_at":990,"expires_at":1010,
                    "nonce":f"http-onboard-nonce-{inner.outer.counter:08d}"}
                return {"authenticated":True,"tenant_id":"tenant-http",
                    "actor_assertion":{"claims":claims,"signature":sign_actor_claims(claims,inner.outer.key)}}
        class EvidenceVerifier:
            def verify(inner, evidence_ref, *, context):
                import hashlib
                digest=hashlib.sha256(evidence_ref.encode()).hexdigest()
                return {"verified":True,"evidence_ref":evidence_ref,
                    "verification_ref":"verification:"+digest[:16],"evidence_digest":"sha256:"+digest}
        self.app=create_app(
            onboarding_administration=InstitutionalOnboardingAdministration(self.registry,self.resolver),
            lifecycle_administration=InstitutionalLifecycleAdministration(self.registry,self.resolver,EvidenceVerifier()),
            institutional_session_resolver=Session(self))
        self.addCleanup(self.app.close)

    def request(self,path,method="GET",body=None):
        raw=b"" if body is None else json.dumps(body).encode()
        env={"REQUEST_METHOD":method,"PATH_INFO":path,"CONTENT_TYPE":"application/json",
             "CONTENT_LENGTH":str(len(raw)),"REMOTE_ADDR":"127.0.0.1","wsgi.input":io.BytesIO(raw)}
        captured={}
        def start(status,headers): captured["status"]=int(status.split()[0])
        result=self.app(env,start)
        return captured,json.loads(b"".join(result))

    def test_authenticated_session_registers_domain_resource_and_reads_audit(self):
        record={"institution_id":"BANK-HTTP","legal_name":"HTTP Bank","jurisdiction_ref":"UG",
                "status":"PENDING_VERIFICATION","verification_ref":None}
        response,body=self.request("/institutional/domain/institutions","POST",{
            "version":"1.0","event_id":"evt-http-register","evidence_ref":"evidence:incorporation","record":record})
        self.assertEqual(response["status"],201,body)
        self.assertEqual(body["registration"]["resource_id"],"BANK-HTTP")
        response,body=self.request("/institutional/domain/institutions/BANK-HTTP")
        self.assertEqual(response["status"],200)
        self.assertEqual(body["resource"]["status"],"PENDING_VERIFICATION")
        response,body=self.request("/institutional/domain/institutions")
        self.assertEqual(response["status"],200)
        self.assertEqual(body["count"],1)
        self.assertEqual(body["resources"][0]["resource_id"],"BANK-HTTP")
        response,body=self.request("/institutional/domain/institutions/BANK-HTTP/events")
        self.assertEqual(response["status"],200)
        self.assertEqual(len(body["events"]),1)

    def test_authenticated_lifecycle_route_verifies_evidence_and_advances_version(self):
        record={"institution_id":"BANK-HTTP","legal_name":"HTTP Bank","jurisdiction_ref":"UG",
                "status":"PENDING_VERIFICATION","verification_ref":None}
        response,body=self.request("/institutional/domain/institutions","POST",{
            "version":"1.0","event_id":"evt-http-life-register","evidence_ref":"evidence:incorporation","record":record})
        self.assertEqual(response["status"],201,body)
        response,body=self.request("/institutional/domain/institutions/BANK-HTTP/lifecycle","POST",{
            "expected_version":"1.0","new_version":"2.0","event_id":"evt-http-life-verify",
            "target_status":"VERIFIED","evidence_ref":"evidence:registry-review",
            "approval_ref":"approval:registry-review"})
        self.assertEqual(response["status"],200,body)
        self.assertTrue(body["transition"]["created"])
        response,body=self.request("/institutional/domain/institutions/BANK-HTTP")
        self.assertEqual(response["status"],200)
        self.assertEqual(body["resource"]["status"],"VERIFIED")
        events=self.registry.list_events(tenant_id="tenant-http",resource_type="institution",resource_id="BANK-HTTP")
        self.assertEqual(len(events),2)
        self.assertIn("evidence_verification",events[-1]["payload"])

    def test_client_cannot_supply_tenant_identity(self):
        response,body=self.request("/institutional/domain/institutions","POST",{
            "version":"1.0","event_id":"evt-http-forged","evidence_ref":"evidence:x",
            "record":{"tenant_id":"tenant-other","institution_id":"BANK-HTTP","legal_name":"Bank",
                "jurisdiction_ref":"UG","status":"PENDING_VERIFICATION","verification_ref":None}})
        self.assertEqual(response["status"],422)
        self.assertEqual(body["error"]["code"],"DOMAIN_RECORD_INVALID")


if __name__=="__main__":
    unittest.main()
