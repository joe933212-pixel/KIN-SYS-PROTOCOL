# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Persistent tenant-scoped authority grants and institutional trust metadata.

This is a SQLite reference registry. Public-key material is represented by a
reference/encoded public key; private keys never belong in this database.
The registry does not itself authenticate issuers or verify grant signatures.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

GRANT_FIELDS = {
    "grant_id", "tenant_id", "status", "actor_ref", "institution_id",
    "authority_ref", "roles", "resource_types", "resource_ids",
    "valid_from", "valid_until", "version", "issuer_institution_id",
    "approval_ref",
}
GRANT_STATUSES = {"PENDING", "ACTIVE", "SUSPENDED", "REVOKED"}
KEY_STATUSES = {"ACTIVE", "SUSPENDED", "REVOKED"}
KEY_ALGORITHMS = {"Ed25519", "HMAC-SHA256"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class TrustRegistryError(ValueError):
    """Invalid or unavailable trust-registry operation."""


class TrustRegistryConflict(TrustRegistryError):
    """An immutable version or trust identifier was reused."""


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TrustRegistryError("VALUE_NOT_CANONICALISABLE") from exc


def _required_identifier(value: Any, code: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise TrustRegistryError(code)
    return value


class InstitutionalTrustRegistry:
    """SQLite reference store for immutable authority grants and trust keys.

    Callers must derive tenant_id from an authenticated principal. Grant
    snapshots are immutable; current-version pointers make revocation visible
    to consumers that resolve grants by ID. Trust-key records contain only
    public material or an external key reference, never private key material.
    """

    def __init__(self, database: str):
        self.db = sqlite3.connect(database, timeout=10.0, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS authority_grant_versions (
          tenant_id TEXT NOT NULL, grant_id TEXT NOT NULL, version TEXT NOT NULL,
          status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, grant_id, version)
        );
        CREATE TABLE IF NOT EXISTS authority_grant_heads (
          tenant_id TEXT NOT NULL, grant_id TEXT NOT NULL, current_version TEXT NOT NULL,
          PRIMARY KEY (tenant_id, grant_id),
          FOREIGN KEY (tenant_id, grant_id, current_version)
            REFERENCES authority_grant_versions(tenant_id, grant_id, version)
        );
        CREATE TABLE IF NOT EXISTS authority_grant_events (
          tenant_id TEXT NOT NULL, event_id TEXT NOT NULL, grant_id TEXT NOT NULL,
          version TEXT NOT NULL, event_type TEXT NOT NULL, actor_ref TEXT NOT NULL,
          evidence_ref TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, event_id),
          FOREIGN KEY (tenant_id, grant_id, version)
            REFERENCES authority_grant_versions(tenant_id, grant_id, version)
        );
        CREATE TABLE IF NOT EXISTS accepted_grant_provenance (
          tenant_id TEXT NOT NULL, grant_id TEXT NOT NULL, version TEXT NOT NULL,
          grant_digest TEXT NOT NULL, issuer_envelope_json TEXT NOT NULL,
          acceptance_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, grant_id, version),
          FOREIGN KEY (tenant_id, grant_id, version)
            REFERENCES authority_grant_versions(tenant_id, grant_id, version)
        );
        CREATE TABLE IF NOT EXISTS trusted_issuer_relationships (
          tenant_id TEXT NOT NULL, issuer_institution_id TEXT NOT NULL,
          status TEXT NOT NULL, trust_basis_ref TEXT NOT NULL,
          valid_from INTEGER NOT NULL, valid_until INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, issuer_institution_id)
        );
        CREATE TABLE IF NOT EXISTS trusted_issuer_events (
          tenant_id TEXT NOT NULL, event_id TEXT NOT NULL,
          issuer_institution_id TEXT NOT NULL, event_type TEXT NOT NULL,
          actor_ref TEXT NOT NULL, evidence_ref TEXT NOT NULL,
          payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, event_id),
          FOREIGN KEY (tenant_id, issuer_institution_id)
            REFERENCES trusted_issuer_relationships(tenant_id, issuer_institution_id)
        );
        CREATE TABLE IF NOT EXISTS institutional_trust_keys (
          tenant_id TEXT NOT NULL, issuer_institution_id TEXT NOT NULL,
          key_id TEXT NOT NULL, algorithm TEXT NOT NULL, public_key_ref TEXT NOT NULL,
          status TEXT NOT NULL, valid_from INTEGER NOT NULL, valid_until INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, key_id)
        );
        CREATE TABLE IF NOT EXISTS institutional_trust_key_events (
          tenant_id TEXT NOT NULL, event_id TEXT NOT NULL, key_id TEXT NOT NULL,
          event_type TEXT NOT NULL, actor_ref TEXT NOT NULL, evidence_ref TEXT NOT NULL,
          payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, event_id),
          FOREIGN KEY (tenant_id, key_id)
            REFERENCES institutional_trust_keys(tenant_id, key_id)
        );
        """)

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def _tenant(tenant_id: str) -> None:
        _required_identifier(tenant_id, "TENANT_CONTEXT_REQUIRED")

    @staticmethod
    def _validate_grant(grant: dict[str, Any]) -> None:
        if not isinstance(grant, dict) or set(grant) != GRANT_FIELDS:
            raise TrustRegistryError("AUTHORITY_GRANT_FIELDS_INVALID")
        for field in ("grant_id", "tenant_id", "actor_ref", "institution_id",
                      "authority_ref", "version", "issuer_institution_id",
                      "approval_ref"):
            _required_identifier(grant[field], "INVALID_GRANT_" + field.upper())
        if grant["status"] not in GRANT_STATUSES:
            raise TrustRegistryError("INVALID_GRANT_STATUS")
        if type(grant["valid_from"]) is not int or type(grant["valid_until"]) is not int:
            raise TrustRegistryError("INVALID_GRANT_VALIDITY")
        if grant["valid_until"] <= grant["valid_from"]:
            raise TrustRegistryError("INVALID_GRANT_VALIDITY")
        for field in ("roles", "resource_types", "resource_ids"):
            values = grant[field]
            if not isinstance(values, list) or not values or any(
                not isinstance(v, str) or not v.strip() for v in values
            ):
                raise TrustRegistryError("INVALID_GRANT_SCOPE")

    def create_grant_version(self, *, tenant_id: str, grant: dict[str, Any],
                             event_id: str, actor_ref: str, evidence_ref: str,
                             event_type: str = "GRANT_VERSION_CREATED") -> dict[str, Any]:
        self._tenant(tenant_id)
        self._validate_grant(grant)
        if grant["tenant_id"] != tenant_id:
            raise TrustRegistryError("GRANT_TENANT_MISMATCH")
        _required_identifier(event_id, "INVALID_EVENT_ID")
        _required_identifier(actor_ref, "INVALID_EVENT_ACTOR")
        _required_identifier(evidence_ref, "GRANT_EVIDENCE_REQUIRED")
        _required_identifier(event_type, "INVALID_EVENT_TYPE")
        body = _canonical(grant)
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute(
                "SELECT payload_json FROM authority_grant_versions WHERE tenant_id=? AND grant_id=? AND version=?",
                (tenant_id, grant["grant_id"], grant["version"])).fetchone()
            if old:
                if old["payload_json"] != body:
                    raise TrustRegistryConflict("IMMUTABLE_GRANT_VERSION_CONFLICT")
                head = self.db.execute(
                    "SELECT current_version FROM authority_grant_heads WHERE tenant_id=? AND grant_id=?",
                    (tenant_id, grant["grant_id"])).fetchone()
                event = self.db.execute(
                    "SELECT grant_id,version,event_type,actor_ref,evidence_ref,payload_json FROM authority_grant_events WHERE tenant_id=? AND event_id=?",
                    (tenant_id, event_id)).fetchone()
                expected_event = (grant["grant_id"], grant["version"], event_type,
                                  actor_ref, evidence_ref, _canonical({"grant_digest_payload": grant}))
                if head and head["current_version"] == grant["version"] and event and tuple(event) == expected_event:
                    self.db.execute("COMMIT")
                    return {"created": False, "version": grant["version"]}
                raise TrustRegistryConflict("GRANT_VERSION_ALREADY_EXISTS")
            head = self.db.execute(
                "SELECT current_version FROM authority_grant_heads WHERE tenant_id=? AND grant_id=?",
                (tenant_id, grant["grant_id"])).fetchone()
            if head:
                raise TrustRegistryConflict("GRANT_VERSION_HEAD_MUST_ADVANCE_EXPLICITLY")
            self.db.execute(
                "INSERT INTO authority_grant_versions VALUES(?,?,?,?,?,?)",
                (tenant_id, grant["grant_id"], grant["version"], grant["status"], body, now))
            self.db.execute(
                "INSERT INTO authority_grant_heads VALUES(?,?,?)",
                (tenant_id, grant["grant_id"], grant["version"]))
            self.db.execute(
                "INSERT INTO authority_grant_events VALUES(?,?,?,?,?,?,?,?,?)",
                (tenant_id, event_id, grant["grant_id"], grant["version"],
                 event_type, actor_ref, evidence_ref,
                 _canonical({"grant_digest_payload": grant}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"created": True, "version": grant["version"]}

    def create_grant_revision(self, *, tenant_id: str, grant_id: str,
                              expected_version: str, grant: dict[str, Any],
                              event_id: str, actor_ref: str, evidence_ref: str,
                              event_type: str = "GRANT_AMENDED") -> dict[str, Any]:
        self._tenant(tenant_id)
        self._validate_grant(grant)
        if grant["tenant_id"] != tenant_id or grant["grant_id"] != grant_id:
            raise TrustRegistryError("GRANT_TENANT_OR_ID_MISMATCH")
        _required_identifier(event_id, "INVALID_EVENT_ID")
        _required_identifier(actor_ref, "INVALID_EVENT_ACTOR")
        _required_identifier(evidence_ref, "GRANT_EVIDENCE_REQUIRED")
        now = datetime.now(timezone.utc).isoformat()
        body = _canonical(grant)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            head = self.db.execute(
                "SELECT current_version FROM authority_grant_heads WHERE tenant_id=? AND grant_id=?",
                (tenant_id, grant_id)).fetchone()
            if head is None:
                raise TrustRegistryError("AUTHORITY_GRANT_NOT_FOUND")
            if head["current_version"] != expected_version:
                raise TrustRegistryConflict("STALE_GRANT_VERSION")
            if self.db.execute(
                "SELECT 1 FROM authority_grant_versions WHERE tenant_id=? AND grant_id=? AND version=?",
                (tenant_id, grant_id, grant["version"])).fetchone():
                raise TrustRegistryConflict("IMMUTABLE_GRANT_VERSION_CONFLICT")
            self.db.execute(
                "INSERT INTO authority_grant_versions VALUES(?,?,?,?,?,?)",
                (tenant_id, grant_id, grant["version"], grant["status"], body, now))
            self.db.execute(
                "UPDATE authority_grant_heads SET current_version=? WHERE tenant_id=? AND grant_id=? AND current_version=?",
                (grant["version"], tenant_id, grant_id, expected_version))
            if self.db.execute("SELECT changes()").fetchone()[0] != 1:
                raise TrustRegistryConflict("STALE_GRANT_VERSION")
            self.db.execute(
                "INSERT INTO authority_grant_events VALUES(?,?,?,?,?,?,?,?,?)",
                (tenant_id, event_id, grant_id, grant["version"], event_type,
                 actor_ref, evidence_ref, _canonical({"grant_digest_payload": grant}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"created": True, "version": grant["version"]}

    def revoke_grant(self, *, tenant_id: str, grant_id: str,
                     expected_version: str, new_version: str, event_id: str,
                     actor_ref: str, evidence_ref: str) -> dict[str, Any]:
        current = self.load_grant(tenant_id, grant_id)
        if current is None:
            raise TrustRegistryError("AUTHORITY_GRANT_NOT_FOUND")
        if current["version"] != expected_version:
            raise TrustRegistryConflict("STALE_GRANT_VERSION")
        if current["status"] == "REVOKED":
            raise TrustRegistryConflict("AUTHORITY_GRANT_ALREADY_REVOKED")
        revoked = dict(current, status="REVOKED", version=new_version,
                       approval_ref=evidence_ref)
        return self.create_grant_revision(
            tenant_id=tenant_id, grant_id=grant_id,
            expected_version=expected_version, grant=revoked,
            event_id=event_id, actor_ref=actor_ref, evidence_ref=evidence_ref,
            event_type="GRANT_REVOKED")

    def load_grant(self, tenant_id: str, grant_id: str) -> dict[str, Any] | None:
        """Resolver-compatible loader; returns only the current grant head.

        Returning the current head (rather than arbitrary historical versions)
        makes a grant revision or revocation invalidate assertions pinned to an
        older version. The resolver still compares the assertion's version.
        """
        self._tenant(tenant_id)
        _required_identifier(grant_id, "INVALID_GRANT_ID")
        row = self.db.execute(
            """SELECT v.payload_json FROM authority_grant_heads h
               JOIN authority_grant_versions v
                 ON v.tenant_id=h.tenant_id AND v.grant_id=h.grant_id
                AND v.version=h.current_version
               WHERE h.tenant_id=? AND h.grant_id=?""",
            (tenant_id, grant_id)).fetchone()
        return json.loads(row["payload_json"]) if row else None


    def load_verified_grant(
        self, tenant_id: str, grant_id: str, *,
        require_federated_provenance: bool,
        public_key_loader: Any,
        clock: Any,
    ) -> dict[str, Any] | None:
        """Load the current grant and verify any retained issuer-signed provenance.

        Local HMAC grants may be used without an issuer envelope only when the
        resolver explicitly selects its local/internal profile. Federated
        assertions require a receiver-accepted issuer-signed grant. Whenever
        provenance exists, it is revalidated against the receiver's current
        trust root, public key, immutable grant snapshot, digest and acceptance
        audit event.
        """
        self._tenant(tenant_id)
        _required_identifier(grant_id, "INVALID_GRANT_ID")
        if type(require_federated_provenance) is not bool or not callable(clock):
            raise TrustRegistryError("VERIFIED_GRANT_REQUEST_INVALID")

        self.db.execute("BEGIN")
        try:
            row = self.db.execute(
                """SELECT v.payload_json FROM authority_grant_heads h
                   JOIN authority_grant_versions v
                     ON v.tenant_id=h.tenant_id AND v.grant_id=h.grant_id
                    AND v.version=h.current_version
                   WHERE h.tenant_id=? AND h.grant_id=?""",
                (tenant_id, grant_id),
            ).fetchone()
            if row is None:
                self.db.execute("COMMIT")
                return None

            grant = json.loads(row["payload_json"])
            self._validate_grant(grant)
            if grant["status"] != "ACTIVE":
                self.db.execute("COMMIT")
                return grant

            provenance = self.db.execute(
                """SELECT grant_digest,issuer_envelope_json,acceptance_json
                   FROM accepted_grant_provenance
                   WHERE tenant_id=? AND grant_id=? AND version=?""",
                (tenant_id, grant_id, grant["version"]),
            ).fetchone()
            if provenance is None:
                if require_federated_provenance:
                    raise TrustRegistryError("FEDERATED_GRANT_PROVENANCE_REQUIRED")
                self.db.execute("COMMIT")
                return grant

            if not callable(public_key_loader):
                raise TrustRegistryError("GRANT_PUBLIC_KEY_RESOLVER_REQUIRED")

            from institutional_grant_provenance import (
                canonical_grant_bytes, verify_authority_grant,
            )

            envelope = json.loads(provenance["issuer_envelope_json"])
            acceptance = json.loads(provenance["acceptance_json"])
            issuer_grant = verify_authority_grant(
                envelope, tenant_id=tenant_id,
                trust_key_loader=self.load_trust_key,
                public_key_loader=public_key_loader,
                clock=clock,
            )
            accepted_snapshot = dict(issuer_grant, status="ACTIVE")
            if accepted_snapshot != grant:
                raise TrustRegistryError("GRANT_PROVENANCE_SNAPSHOT_MISMATCH")

            digest = hashlib.sha256(canonical_grant_bytes(issuer_grant)).hexdigest()
            if provenance["grant_digest"] != digest:
                raise TrustRegistryError("GRANT_PROVENANCE_DIGEST_MISMATCH")

            acceptance_fields = {
                "receiver_institution_id", "grant_id", "grant_version",
                "grant_digest", "receiver_approval",
            }
            if not isinstance(acceptance, dict) or set(acceptance) != acceptance_fields:
                raise TrustRegistryError("GRANT_ACCEPTANCE_RECORD_INVALID")
            approval = acceptance["receiver_approval"]
            if not isinstance(approval, dict):
                raise TrustRegistryError("GRANT_ACCEPTANCE_APPROVAL_INVALID")
            approval_actor = approval.get("actor_ref")
            approval_ref = approval.get("approval_ref")
            _required_identifier(approval_actor, "GRANT_ACCEPTANCE_ACTOR_INVALID")
            _required_identifier(approval_ref, "GRANT_ACCEPTANCE_EVIDENCE_INVALID")
            if (
                acceptance["receiver_institution_id"] != grant["institution_id"]
                or acceptance["grant_id"] != grant["grant_id"]
                or acceptance["grant_version"] != grant["version"]
                or acceptance["grant_digest"] != digest
            ):
                raise TrustRegistryError("GRANT_ACCEPTANCE_SCOPE_MISMATCH")

            events = self.db.execute(
                """SELECT actor_ref,evidence_ref,payload_json
                   FROM authority_grant_events
                   WHERE tenant_id=? AND grant_id=? AND version=?
                     AND event_type='FEDERATED_GRANT_ACCEPTED'""",
                (tenant_id, grant_id, grant["version"]),
            ).fetchall()
            if len(events) != 1:
                raise TrustRegistryError("GRANT_ACCEPTANCE_AUDIT_EVENT_MISSING_OR_AMBIGUOUS")
            event = events[0]
            expected_payload = {
                "grant_digest": digest,
                "issuer_envelope": envelope,
                "acceptance": acceptance,
            }
            if json.loads(event["payload_json"]) != expected_payload:
                raise TrustRegistryError("GRANT_ACCEPTANCE_AUDIT_PAYLOAD_MISMATCH")
            if event["actor_ref"] != approval_actor or event["evidence_ref"] != approval_ref:
                raise TrustRegistryError("GRANT_ACCEPTANCE_AUDIT_CONTEXT_MISMATCH")

            self.db.execute("COMMIT")
            return grant
        except TrustRegistryError:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        except Exception as exc:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise TrustRegistryError("GRANT_PROVENANCE_VERIFICATION_FAILED") from exc

    def list_grant_events(self, *, tenant_id: str, grant_id: str) -> list[dict[str, Any]]:
        self._tenant(tenant_id)
        _required_identifier(grant_id, "INVALID_GRANT_ID")
        rows = self.db.execute(
            """SELECT event_id,version,event_type,actor_ref,evidence_ref,payload_json,created_at
               FROM authority_grant_events WHERE tenant_id=? AND grant_id=?
               ORDER BY created_at,event_id""", (tenant_id, grant_id)).fetchall()
        return [{"event_id": r["event_id"], "version": r["version"],
                 "event_type": r["event_type"], "actor_ref": r["actor_ref"],
                 "evidence_ref": r["evidence_ref"], "payload": json.loads(r["payload_json"]),
                 "created_at": r["created_at"]} for r in rows]


    def accept_signed_grant(self, *, tenant_id: str, envelope: dict[str, Any],
                            receiver_institution_id: str,
                            receiver_approval: dict[str, Any],
                            verify_receiver_approval: Any,
                            public_key_loader: Any, event_id: str,
                            actor_ref: str, evidence_ref: str,
                            clock: Any = None) -> dict[str, Any]:
        """Verify, accept and persist a federated grant in one SQLite transaction.

        The receiver approval adapter must authenticate the receiver-side actor
        and enforce its current authority policy. The caller must derive
        tenant_id and actor_ref from authenticated request context. This method
        stores the original issuer envelope and receiver acceptance evidence
        alongside the immutable ACTIVE grant snapshot.
        """
        from institutional_grant_provenance import accept_authority_grant
        self._tenant(tenant_id)
        for value, code in ((receiver_institution_id, "INVALID_RECEIVER_INSTITUTION"),
                            (event_id, "INVALID_EVENT_ID"),
                            (actor_ref, "INVALID_EVENT_ACTOR"),
                            (evidence_ref, "GRANT_EVIDENCE_REQUIRED")):
            _required_identifier(value, code)
        if not isinstance(receiver_approval, dict):
            raise TrustRegistryError("RECEIVER_APPROVAL_REQUIRED")
        if (receiver_approval.get("actor_ref") != actor_ref
                or receiver_approval.get("approval_ref") != evidence_ref):
            raise TrustRegistryError("RECEIVER_APPROVAL_AUDIT_CONTEXT_MISMATCH")
        kwargs = {
            "envelope": envelope, "tenant_id": tenant_id,
            "receiver_institution_id": receiver_institution_id,
            "trust_key_loader": self.load_trust_key,
            "public_key_loader": public_key_loader,
            "verify_receiver_approval": verify_receiver_approval,
            "receiver_approval": receiver_approval,
        }
        if clock is not None:
            kwargs["clock"] = clock
        try:
            accepted = accept_authority_grant(**kwargs)
        except Exception as exc:
            raise TrustRegistryError("SIGNED_GRANT_ACCEPTANCE_REJECTED:" + str(exc)) from exc
        grant = accepted["grant"]
        self._validate_grant(grant)
        if grant["tenant_id"] != tenant_id or grant["status"] != "ACTIVE":
            raise TrustRegistryError("ACCEPTED_GRANT_SCOPE_INVALID")
        body = _canonical(grant)
        envelope_json = _canonical(accepted["issuer_envelope"])
        acceptance_json = _canonical(accepted["acceptance"])
        digest = accepted["acceptance"]["grant_digest"]
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            head = self.db.execute(
                "SELECT current_version FROM authority_grant_heads WHERE tenant_id=? AND grant_id=?",
                (tenant_id, grant["grant_id"])).fetchone()
            if head is not None:
                raise TrustRegistryConflict("AUTHORITY_GRANT_ALREADY_EXISTS")
            self.db.execute(
                "INSERT INTO authority_grant_versions VALUES(?,?,?,?,?,?)",
                (tenant_id, grant["grant_id"], grant["version"], grant["status"], body, now))
            self.db.execute(
                "INSERT INTO authority_grant_heads VALUES(?,?,?)",
                (tenant_id, grant["grant_id"], grant["version"]))
            self.db.execute(
                "INSERT INTO accepted_grant_provenance VALUES(?,?,?,?,?,?,?)",
                (tenant_id, grant["grant_id"], grant["version"], digest,
                 envelope_json, acceptance_json, now))
            self.db.execute(
                "INSERT INTO authority_grant_events VALUES(?,?,?,?,?,?,?,?,?)",
                (tenant_id, event_id, grant["grant_id"], grant["version"],
                 "FEDERATED_GRANT_ACCEPTED", actor_ref, evidence_ref,
                 _canonical({"grant_digest": digest,
                             "issuer_envelope": accepted["issuer_envelope"],
                             "acceptance": accepted["acceptance"]}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"accepted": True, "grant": grant, "grant_digest": digest,
                "version": grant["version"]}

    def load_grant_provenance(self, *, tenant_id: str, grant_id: str,
                              version: str) -> dict[str, Any] | None:
        """Return the retained issuer envelope and receiver acceptance record."""
        self._tenant(tenant_id)
        _required_identifier(grant_id, "INVALID_GRANT_ID")
        _required_identifier(version, "INVALID_GRANT_VERSION")
        row = self.db.execute(
            """SELECT grant_digest,issuer_envelope_json,acceptance_json,created_at
               FROM accepted_grant_provenance
               WHERE tenant_id=? AND grant_id=? AND version=?""",
            (tenant_id, grant_id, version)).fetchone()
        if row is None:
            return None
        return {"grant_digest": row["grant_digest"],
                "issuer_envelope": json.loads(row["issuer_envelope_json"]),
                "acceptance": json.loads(row["acceptance_json"]),
                "created_at": row["created_at"]}

    def register_trusted_issuer(self, *, tenant_id: str, issuer_institution_id: str,
                                trust_basis_ref: str, valid_from: int, valid_until: int,
                                actor_ref: str, evidence_ref: str) -> dict[str, Any]:
        """Establish this tenant's explicit trust relationship with an issuer."""
        self._tenant(tenant_id)
        for value, code in ((issuer_institution_id, "INVALID_ISSUER_ID"),
                            (actor_ref, "INVALID_EVENT_ACTOR"),
                            (evidence_ref, "TRUST_EVIDENCE_REQUIRED")):
            _required_identifier(value, code)
        if not isinstance(trust_basis_ref, str) or not trust_basis_ref.strip():
            raise TrustRegistryError("TRUST_BASIS_REFERENCE_REQUIRED")
        if type(valid_from) is not int or type(valid_until) is not int or valid_until <= valid_from:
            raise TrustRegistryError("INVALID_TRUST_RELATIONSHIP_VALIDITY")
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            try:
                self.db.execute(
                    "INSERT INTO trusted_issuer_relationships VALUES(?,?,?,?,?,?,?)",
                    (tenant_id, issuer_institution_id, "ACTIVE", trust_basis_ref,
                     valid_from, valid_until, now))
            except sqlite3.IntegrityError as exc:
                raise TrustRegistryConflict("ISSUER_TRUST_RELATIONSHIP_ALREADY_EXISTS") from exc
            self.db.execute(
                "INSERT INTO trusted_issuer_events VALUES(?,?,?,?,?,?,?,?)",
                (tenant_id, "issuer-trust:" + issuer_institution_id,
                 issuer_institution_id, "ISSUER_TRUST_ESTABLISHED", actor_ref,
                 evidence_ref, _canonical({"trust_basis_ref": trust_basis_ref}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"trusted": True, "issuer_institution_id": issuer_institution_id}

    def revoke_trusted_issuer(self, *, tenant_id: str, issuer_institution_id: str,
                              actor_ref: str, evidence_ref: str,
                              event_id: str) -> dict[str, Any]:
        self._tenant(tenant_id)
        for value, code in ((issuer_institution_id, "INVALID_ISSUER_ID"),
                            (actor_ref, "INVALID_EVENT_ACTOR"),
                            (evidence_ref, "TRUST_EVIDENCE_REQUIRED"),
                            (event_id, "INVALID_EVENT_ID")):
            _required_identifier(value, code)
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT status FROM trusted_issuer_relationships WHERE tenant_id=? AND issuer_institution_id=?",
                (tenant_id, issuer_institution_id)).fetchone()
            if row is None:
                raise TrustRegistryError("TRUSTED_ISSUER_NOT_FOUND")
            if row["status"] == "REVOKED":
                raise TrustRegistryConflict("ISSUER_TRUST_ALREADY_REVOKED")
            self.db.execute(
                "UPDATE trusted_issuer_relationships SET status='REVOKED' WHERE tenant_id=? AND issuer_institution_id=?",
                (tenant_id, issuer_institution_id))
            self.db.execute(
                "INSERT INTO trusted_issuer_events VALUES(?,?,?,?,?,?,?,?)",
                (tenant_id, event_id, issuer_institution_id, "ISSUER_TRUST_REVOKED",
                 actor_ref, evidence_ref, _canonical({"status": "REVOKED"}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"revoked": True, "issuer_institution_id": issuer_institution_id}

    def load_trust_key(self, tenant_id: str, key_id: str) -> dict[str, Any] | None:
        """Resolver adapter: return only keys under an active issuer trust root."""
        self._tenant(tenant_id)
        _required_identifier(key_id, "INVALID_KEY_ID")
        row = self.db.execute(
            """SELECT k.tenant_id,k.issuer_institution_id,k.key_id,k.algorithm,
                      k.public_key_ref,k.status,k.valid_from,k.valid_until,
                      i.valid_from AS issuer_valid_from,
                      i.valid_until AS issuer_valid_until
               FROM institutional_trust_keys k
               JOIN trusted_issuer_relationships i
                 ON i.tenant_id=k.tenant_id
                AND i.issuer_institution_id=k.issuer_institution_id
               WHERE k.tenant_id=? AND k.key_id=? AND i.status='ACTIVE'""",
            (tenant_id, key_id)).fetchone()
        return dict(row) if row else None

    def list_trusted_issuer_events(self, *, tenant_id: str,
                                  issuer_institution_id: str) -> list[dict[str, Any]]:
        self._tenant(tenant_id)
        _required_identifier(issuer_institution_id, "INVALID_ISSUER_ID")
        rows = self.db.execute(
            """SELECT event_id,event_type,actor_ref,evidence_ref,payload_json,created_at
               FROM trusted_issuer_events
               WHERE tenant_id=? AND issuer_institution_id=?
               ORDER BY created_at,event_id""",
            (tenant_id, issuer_institution_id)).fetchall()
        return [{"event_id": r["event_id"], "event_type": r["event_type"],
                 "actor_ref": r["actor_ref"], "evidence_ref": r["evidence_ref"],
                 "payload": json.loads(r["payload_json"]), "created_at": r["created_at"]}
                for r in rows]

    def register_trust_key(self, *, tenant_id: str, issuer_institution_id: str,
                           key_id: str, algorithm: str, public_key_ref: str,
                           valid_from: int, valid_until: int,
                           actor_ref: str, evidence_ref: str) -> dict[str, Any]:
        self._tenant(tenant_id)
        for value, code in ((issuer_institution_id, "INVALID_ISSUER_ID"),
                            (key_id, "INVALID_KEY_ID"),
                            (actor_ref, "INVALID_EVENT_ACTOR"),
                            (evidence_ref, "TRUST_EVIDENCE_REQUIRED")):
            _required_identifier(value, code)
        if (not isinstance(public_key_ref, str) or not public_key_ref.strip()
                or any(ord(ch) < 32 for ch in public_key_ref)):
            raise TrustRegistryError("PUBLIC_KEY_REFERENCE_REQUIRED")
        if algorithm not in KEY_ALGORITHMS:
            raise TrustRegistryError("UNSUPPORTED_TRUST_KEY_ALGORITHM")
        if type(valid_from) is not int or type(valid_until) is not int or valid_until <= valid_from:
            raise TrustRegistryError("INVALID_TRUST_KEY_VALIDITY")
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            try:
                self.db.execute(
                    "INSERT INTO institutional_trust_keys VALUES(?,?,?,?,?,?,?,?,?)",
                    (tenant_id, issuer_institution_id, key_id, algorithm,
                     public_key_ref, "ACTIVE", valid_from, valid_until, now))
            except sqlite3.IntegrityError as exc:
                raise TrustRegistryConflict("TRUST_KEY_ID_ALREADY_REGISTERED") from exc
            self.db.execute(
                "INSERT INTO institutional_trust_key_events VALUES(?,?,?,?,?,?,?,?)",
                (tenant_id, "key-register:" + key_id, key_id, "TRUST_KEY_REGISTERED",
                 actor_ref, evidence_ref, _canonical({"issuer_institution_id": issuer_institution_id,
                 "algorithm": algorithm, "public_key_ref": public_key_ref}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"created": True, "key_id": key_id}

    def get_trust_key(self, *, tenant_id: str, key_id: str) -> dict[str, Any] | None:
        self._tenant(tenant_id)
        _required_identifier(key_id, "INVALID_KEY_ID")
        row = self.db.execute(
            """SELECT issuer_institution_id,key_id,algorithm,public_key_ref,status,
                      valid_from,valid_until,created_at
               FROM institutional_trust_keys WHERE tenant_id=? AND key_id=?""",
            (tenant_id, key_id)).fetchone()
        return dict(row) if row else None

    def revoke_trust_key(self, *, tenant_id: str, key_id: str, actor_ref: str,
                         evidence_ref: str, event_id: str) -> dict[str, Any]:
        self._tenant(tenant_id)
        for value, code in ((key_id, "INVALID_KEY_ID"), (actor_ref, "INVALID_EVENT_ACTOR"),
                            (evidence_ref, "TRUST_EVIDENCE_REQUIRED"),
                            (event_id, "INVALID_EVENT_ID")):
            _required_identifier(value, code)
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT status FROM institutional_trust_keys WHERE tenant_id=? AND key_id=?",
                (tenant_id, key_id)).fetchone()
            if row is None:
                raise TrustRegistryError("TRUST_KEY_NOT_FOUND")
            if row["status"] == "REVOKED":
                raise TrustRegistryConflict("TRUST_KEY_ALREADY_REVOKED")
            self.db.execute(
                "UPDATE institutional_trust_keys SET status='REVOKED' WHERE tenant_id=? AND key_id=?",
                (tenant_id, key_id))
            self.db.execute(
                "INSERT INTO institutional_trust_key_events VALUES(?,?,?,?,?,?,?,?)",
                (tenant_id, event_id, key_id, "TRUST_KEY_REVOKED", actor_ref,
                 evidence_ref, _canonical({"status": "REVOKED"}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"revoked": True, "key_id": key_id}

    def list_trust_key_events(self, *, tenant_id: str, key_id: str) -> list[dict[str, Any]]:
        self._tenant(tenant_id)
        _required_identifier(key_id, "INVALID_KEY_ID")
        rows = self.db.execute(
            """SELECT event_id,event_type,actor_ref,evidence_ref,payload_json,created_at
               FROM institutional_trust_key_events WHERE tenant_id=? AND key_id=?
               ORDER BY created_at,event_id""", (tenant_id, key_id)).fetchall()
        return [{"event_id": r["event_id"], "event_type": r["event_type"],
                 "actor_ref": r["actor_ref"], "evidence_ref": r["evidence_ref"],
                 "payload": json.loads(r["payload_json"]), "created_at": r["created_at"]}
                for r in rows]
