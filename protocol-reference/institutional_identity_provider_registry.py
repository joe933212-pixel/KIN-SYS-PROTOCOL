# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Tenant-scoped, versioned institutional identity-provider configuration.

This registry stores public protocol policy only. Client secrets, signing keys,
certificates with private material and other credentials belong in a secret
manager or the institution's identity provider. Lifecycle authorisation is an
application responsibility and must precede calls to transition_provider().
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ALGORITHMS = {"RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"}
AMR = {"pwd", "otp", "hwk", "swk", "mfa", "fido", "x509", "sms", "face", "iris"}
STATUSES = {"PENDING", "ACTIVE", "SUSPENDED", "REVOKED"}
FIELDS = {
    "tenant_id", "provider_id", "version", "protocol", "issuer", "client_id",
    "status", "allowed_hosts", "allowed_algorithms", "audiences", "allowed_acr",
    "required_amr", "valid_from", "valid_until", "cache_ttl_seconds",
}


class IdentityProviderRegistryError(ValueError):
    """Invalid provider configuration or unavailable tenant-scoped record."""


class IdentityProviderRegistryConflict(IdentityProviderRegistryError):
    """Immutable version conflict or stale expected version."""


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise IdentityProviderRegistryError("PROVIDER_CONFIG_NOT_CANONICAL") from exc


def _id(value: Any, code: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise IdentityProviderRegistryError(code)
    return value


def _validate(config: Any) -> None:
    if not isinstance(config, dict) or set(config) != FIELDS:
        raise IdentityProviderRegistryError("PROVIDER_CONFIG_FIELDS_INVALID")
    for field in ("tenant_id", "provider_id", "version"):
        _id(config[field], "PROVIDER_" + field.upper() + "_INVALID")
    if config["protocol"] != "OIDC":
        raise IdentityProviderRegistryError("PROVIDER_PROTOCOL_UNSUPPORTED")
    if not isinstance(config["status"], str) or config["status"] not in STATUSES:
        raise IdentityProviderRegistryError("PROVIDER_STATUS_INVALID")
    issuer = config["issuer"]
    try:
        parsed = urlsplit(issuer)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise IdentityProviderRegistryError("PROVIDER_ISSUER_INVALID") from exc
    if (not isinstance(issuer, str) or len(issuer) > 2048
            or parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or port not in (None, 443)
            or "\\" in issuer or any(ord(ch) < 0x20 for ch in issuer)):
        raise IdentityProviderRegistryError("PROVIDER_ISSUER_INVALID")
    if (not isinstance(config["client_id"], str) or not config["client_id"]
            or len(config["client_id"]) > 512
            or any(ord(ch) < 0x20 for ch in config["client_id"])):
        raise IdentityProviderRegistryError("PROVIDER_CLIENT_ID_INVALID")
    hosts = config["allowed_hosts"]
    if (not isinstance(hosts, list) or not hosts
            or any(not isinstance(h, str) or not h or h != h.lower()
                   or h.endswith(".") or "/" in h or ":" in h
                   or any(ord(ch) < 0x21 for ch in h) for h in hosts)
            or len(set(hosts)) != len(hosts)
            or parsed.hostname.lower() not in hosts):
        raise IdentityProviderRegistryError("PROVIDER_HOST_ALLOWLIST_INVALID")
    algorithms = config["allowed_algorithms"]
    if (not isinstance(algorithms, list) or not algorithms
            or any(not isinstance(a, str) or a not in ALGORITHMS for a in algorithms)
            or len(set(algorithms)) != len(algorithms)):
        raise IdentityProviderRegistryError("PROVIDER_ALGORITHM_POLICY_INVALID")
    audiences = config["audiences"]
    if (not isinstance(audiences, list) or not audiences
            or any(not isinstance(a, str) or not a or len(a) > 512 for a in audiences)
            or len(set(audiences)) != len(audiences)
            or config["client_id"] not in audiences):
        raise IdentityProviderRegistryError("PROVIDER_AUDIENCE_POLICY_INVALID")
    acr = config["allowed_acr"]
    if (not isinstance(acr, list) or not acr
            or any(not isinstance(x, str) or not x or len(x) > 2048 for x in acr)
            or len(set(acr)) != len(acr)):
        raise IdentityProviderRegistryError("PROVIDER_ACR_POLICY_INVALID")
    required_amr = config["required_amr"]
    if (not isinstance(required_amr, list)
            or any(not isinstance(x, str) or x not in AMR for x in required_amr)
            or len(set(required_amr)) != len(required_amr)):
        raise IdentityProviderRegistryError("PROVIDER_AMR_POLICY_INVALID")
    if (type(config["valid_from"]) is not int or type(config["valid_until"]) is not int
            or config["valid_until"] <= config["valid_from"]):
        raise IdentityProviderRegistryError("PROVIDER_VALIDITY_INVALID")
    if type(config["cache_ttl_seconds"]) is not int or not 1 <= config["cache_ttl_seconds"] <= 3600:
        raise IdentityProviderRegistryError("PROVIDER_CACHE_POLICY_INVALID")


class InstitutionalIdentityProviderRegistry:
    """Append-only provider configuration versions with a tenant-local head."""

    def __init__(self, database: str):
        self.db = sqlite3.connect(database, timeout=10.0, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS identity_provider_versions (
          tenant_id TEXT NOT NULL, provider_id TEXT NOT NULL, version TEXT NOT NULL,
          status TEXT NOT NULL, config_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, provider_id, version)
        );
        CREATE TABLE IF NOT EXISTS identity_provider_heads (
          tenant_id TEXT NOT NULL, provider_id TEXT NOT NULL, current_version TEXT NOT NULL,
          PRIMARY KEY (tenant_id, provider_id),
          FOREIGN KEY (tenant_id, provider_id, current_version)
            REFERENCES identity_provider_versions(tenant_id, provider_id, version)
        );
        CREATE TABLE IF NOT EXISTS identity_provider_events (
          tenant_id TEXT NOT NULL, event_id TEXT NOT NULL, provider_id TEXT NOT NULL,
          version TEXT NOT NULL, event_type TEXT NOT NULL, actor_ref TEXT NOT NULL,
          evidence_ref TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, event_id),
          FOREIGN KEY (tenant_id, provider_id, version)
            REFERENCES identity_provider_versions(tenant_id, provider_id, version)
        );
        """)

    def close(self) -> None:
        self.db.close()

    def register_provider(self, *, tenant_id: str, config: dict[str, Any],
                          event_id: str, actor_ref: str,
                          evidence_ref: str) -> dict[str, Any]:
        _id(tenant_id, "TENANT_CONTEXT_REQUIRED")
        _validate(config)
        if config["tenant_id"] != tenant_id:
            raise IdentityProviderRegistryError("PROVIDER_TENANT_MISMATCH")
        if config["status"] != "PENDING":
            raise IdentityProviderRegistryError("NEW_PROVIDER_MUST_START_PENDING")
        event_id = _id(event_id, "PROVIDER_EVENT_ID_INVALID")
        actor_ref = _id(actor_ref, "PROVIDER_EVENT_ACTOR_INVALID")
        evidence_ref = _id(evidence_ref, "PROVIDER_EVIDENCE_REQUIRED")
        body = _canonical(config)
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = self.db.execute(
                "SELECT current_version FROM identity_provider_heads WHERE tenant_id=? AND provider_id=?",
                (tenant_id, config["provider_id"])).fetchone()
            if existing:
                raise IdentityProviderRegistryConflict("PROVIDER_ALREADY_REGISTERED")
            self.db.execute(
                "INSERT INTO identity_provider_versions VALUES(?,?,?,?,?,?)",
                (tenant_id, config["provider_id"], config["version"],
                 config["status"], body, now))
            self.db.execute(
                "INSERT INTO identity_provider_heads VALUES(?,?,?)",
                (tenant_id, config["provider_id"], config["version"]))
            self.db.execute(
                "INSERT INTO identity_provider_events VALUES(?,?,?,?,?,?,?,?,?)",
                (tenant_id, event_id, config["provider_id"], config["version"],
                 "PROVIDER_REGISTERED", actor_ref, evidence_ref,
                 _canonical({"config_digest_payload": config}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"created": True, "version": config["version"]}


    def revise_provider_config(self, *, tenant_id: str, provider_id: str,
                               expected_version: str, config: dict[str, Any],
                               event_id: str, actor_ref: str,
                               evidence_ref: str) -> dict[str, Any]:
        """Install a new pending config version; it is not usable until re-approved."""
        tenant_id = _id(tenant_id, "TENANT_CONTEXT_REQUIRED")
        provider_id = _id(provider_id, "PROVIDER_ID_INVALID")
        expected_version = _id(expected_version, "PROVIDER_VERSION_INVALID")
        _validate(config)
        if (config["tenant_id"] != tenant_id or config["provider_id"] != provider_id
                or config["status"] != "PENDING" or config["version"] == expected_version):
            raise IdentityProviderRegistryError("PROVIDER_REVISION_SCOPE_INVALID")
        event_id = _id(event_id, "PROVIDER_EVENT_ID_INVALID")
        actor_ref = _id(actor_ref, "PROVIDER_EVENT_ACTOR_INVALID")
        evidence_ref = _id(evidence_ref, "PROVIDER_EVIDENCE_REQUIRED")
        body = _canonical(config)
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            head = self.db.execute(
                "SELECT current_version FROM identity_provider_heads WHERE tenant_id=? AND provider_id=?",
                (tenant_id, provider_id)).fetchone()
            if head is None:
                raise IdentityProviderRegistryError("PROVIDER_NOT_FOUND")
            if head["current_version"] != expected_version:
                raise IdentityProviderRegistryConflict("STALE_PROVIDER_VERSION")
            if self.db.execute(
                "SELECT 1 FROM identity_provider_versions WHERE tenant_id=? AND provider_id=? AND version=?",
                (tenant_id, provider_id, config["version"])).fetchone():
                raise IdentityProviderRegistryConflict("PROVIDER_VERSION_ALREADY_EXISTS")
            self.db.execute(
                "INSERT INTO identity_provider_versions VALUES(?,?,?,?,?,?)",
                (tenant_id, provider_id, config["version"], "PENDING", body, now))
            self.db.execute(
                "UPDATE identity_provider_heads SET current_version=? WHERE tenant_id=? AND provider_id=? AND current_version=?",
                (config["version"], tenant_id, provider_id, expected_version))
            self.db.execute(
                "INSERT INTO identity_provider_events VALUES(?,?,?,?,?,?,?,?,?)",
                (tenant_id, event_id, provider_id, config["version"],
                 "PROVIDER_CONFIGURATION_REVISED", actor_ref, evidence_ref,
                 _canonical({"from_version": expected_version,
                             "to_version": config["version"]}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"created": True, "version": config["version"], "status": "PENDING"}

    def transition_provider(self, *, tenant_id: str, provider_id: str,
                            expected_version: str, new_version: str,
                            new_status: str, event_id: str, actor_ref: str,
                            evidence_ref: str) -> dict[str, Any]:
        """Create a new immutable status snapshot after caller-side authorisation."""
        tenant_id = _id(tenant_id, "TENANT_CONTEXT_REQUIRED")
        provider_id = _id(provider_id, "PROVIDER_ID_INVALID")
        expected_version = _id(expected_version, "PROVIDER_VERSION_INVALID")
        new_version = _id(new_version, "PROVIDER_VERSION_INVALID")
        event_id = _id(event_id, "PROVIDER_EVENT_ID_INVALID")
        actor_ref = _id(actor_ref, "PROVIDER_EVENT_ACTOR_INVALID")
        evidence_ref = _id(evidence_ref, "PROVIDER_EVIDENCE_REQUIRED")
        if not isinstance(new_status, str) or new_status not in STATUSES:
            raise IdentityProviderRegistryError("PROVIDER_STATUS_INVALID")
        if new_version == expected_version:
            raise IdentityProviderRegistryConflict("PROVIDER_VERSION_MUST_ADVANCE")
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            head = self.db.execute(
                "SELECT current_version FROM identity_provider_heads WHERE tenant_id=? AND provider_id=?",
                (tenant_id, provider_id)).fetchone()
            if head is None:
                raise IdentityProviderRegistryError("PROVIDER_NOT_FOUND")
            if head["current_version"] != expected_version:
                raise IdentityProviderRegistryConflict("STALE_PROVIDER_VERSION")
            current = self.db.execute(
                "SELECT config_json FROM identity_provider_versions WHERE tenant_id=? AND provider_id=? AND version=?",
                (tenant_id, provider_id, expected_version)).fetchone()
            config = json.loads(current["config_json"])
            old_status = config["status"]
            if self.db.execute(
                "SELECT 1 FROM identity_provider_versions WHERE tenant_id=? AND provider_id=? AND version=?",
                (tenant_id, provider_id, new_version)).fetchone():
                raise IdentityProviderRegistryConflict("PROVIDER_VERSION_ALREADY_EXISTS")
            allowed = {
                "PENDING": {"ACTIVE", "SUSPENDED", "REVOKED"},
                "ACTIVE": {"SUSPENDED", "REVOKED"},
                "SUSPENDED": {"ACTIVE", "REVOKED"},
                "REVOKED": set(),
            }
            if new_status not in allowed[old_status]:
                raise IdentityProviderRegistryError("PROVIDER_TRANSITION_NOT_ALLOWED")
            config["version"] = new_version
            config["status"] = new_status
            _validate(config)
            body = _canonical(config)
            self.db.execute(
                "INSERT INTO identity_provider_versions VALUES(?,?,?,?,?,?)",
                (tenant_id, provider_id, new_version, new_status, body, now))
            self.db.execute(
                "UPDATE identity_provider_heads SET current_version=? WHERE tenant_id=? AND provider_id=? AND current_version=?",
                (new_version, tenant_id, provider_id, expected_version))
            event_type = "PROVIDER_" + new_status
            self.db.execute(
                "INSERT INTO identity_provider_events VALUES(?,?,?,?,?,?,?,?,?)",
                (tenant_id, event_id, provider_id, new_version, event_type,
                 actor_ref, evidence_ref,
                 _canonical({"from_status": old_status, "to_status": new_status,
                             "expected_version": expected_version}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"created": True, "version": new_version, "status": new_status}

    def get_provider(self, *, tenant_id: str, provider_id: str,
                     version: str | None = None) -> dict[str, Any] | None:
        tenant_id = _id(tenant_id, "TENANT_CONTEXT_REQUIRED")
        provider_id = _id(provider_id, "PROVIDER_ID_INVALID")
        if version is None:
            row = self.db.execute(
                """SELECT v.version,v.config_json FROM identity_provider_heads h
                   JOIN identity_provider_versions v
                   ON v.tenant_id=h.tenant_id AND v.provider_id=h.provider_id
                   AND v.version=h.current_version
                   WHERE h.tenant_id=? AND h.provider_id=?""",
                (tenant_id, provider_id)).fetchone()
        else:
            version = _id(version, "PROVIDER_VERSION_INVALID")
            row = self.db.execute(
                "SELECT version,config_json FROM identity_provider_versions WHERE tenant_id=? AND provider_id=? AND version=?",
                (tenant_id, provider_id, version)).fetchone()
        return json.loads(row["config_json"]) if row else None

    def resolve_active_provider(self, tenant_id: str, provider_id: str,
                                *, now: int | None = None) -> dict[str, Any] | None:
        config = self.get_provider(tenant_id=tenant_id, provider_id=provider_id)
        if not config or config["status"] != "ACTIVE":
            return None
        instant = int(__import__("time").time()) if now is None else now
        if type(instant) is not int:
            raise IdentityProviderRegistryError("PROVIDER_CLOCK_INVALID")
        if not config["valid_from"] <= instant < config["valid_until"]:
            return None
        return config


    def resolve_active_issuer_policy(self, tenant_id: str, issuer: str,
                                     *, now: int | None = None) -> dict[str, Any] | None:
        """Return receiver policy only when exactly one active provider matches issuer."""
        tenant_id = _id(tenant_id, "TENANT_CONTEXT_REQUIRED")
        if not isinstance(issuer, str) or not issuer:
            raise IdentityProviderRegistryError("PROVIDER_ISSUER_INVALID")
        instant = int(__import__("time").time()) if now is None else now
        if type(instant) is not int:
            raise IdentityProviderRegistryError("PROVIDER_CLOCK_INVALID")
        rows = self.db.execute(
            """SELECT v.config_json FROM identity_provider_heads h
               JOIN identity_provider_versions v
               ON v.tenant_id=h.tenant_id AND v.provider_id=h.provider_id
               AND v.version=h.current_version
               WHERE h.tenant_id=? AND v.status='ACTIVE'""",
            (tenant_id,)).fetchall()
        configs = [json.loads(row["config_json"]) for row in rows]
        matches = [config for config in configs if config.get("issuer") == issuer]
        if len(matches) != 1:
            return None
        config = matches[0]
        if not config["valid_from"] <= instant < config["valid_until"]:
            return None
        return {
            "issuer": config["issuer"], "status": config["status"],
            "audiences": list(config["audiences"]),
            "allowed_acr": list(config["allowed_acr"]),
            "required_amr": list(config["required_amr"]),
            "valid_from": config["valid_from"],
            "valid_until": config["valid_until"],
        }

    def list_events(self, *, tenant_id: str, provider_id: str) -> list[dict[str, Any]]:
        tenant_id = _id(tenant_id, "TENANT_CONTEXT_REQUIRED")
        provider_id = _id(provider_id, "PROVIDER_ID_INVALID")
        rows = self.db.execute(
            "SELECT event_id,version,event_type,actor_ref,evidence_ref,payload_json,created_at FROM identity_provider_events WHERE tenant_id=? AND provider_id=? ORDER BY created_at,rowid",
            (tenant_id, provider_id)).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]
