# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Tenant-scoped institutional MINAH deployment registration and lifecycle.

This reference layer registers an institution-controlled, provider-neutral
agent descriptor and pins its identity-provider, mandate, jurisdiction,
policy, source and authority-profile versions. It does not host or execute an
AI model, authenticate administrators, establish legal authority, or replace
the MINAH -> Identity -> Authority -> CEE -> Final Decision runtime boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

AGENT_KINDS = {"INTERNAL", "THIRD_PARTY_HOSTED", "SOVEREIGN"}
DEPLOYMENT_MODES = {"SELF_HOSTED", "ON_PREMISES", "CLOUD", "EDGE", "MANAGED"}
STATUSES = {"PENDING", "ACTIVE", "SUSPENDED", "REVOKED"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
FIELDS = {
    "deployment_id", "version", "status", "agent_kind", "agent_ref",
    "interface_version", "deployment_mode", "model_ref", "endpoint_ref",
    "secret_ref", "owner_scope", "owner_ref", "jurisdictions",
    "identity_provider_id", "identity_provider_version", "jurisdiction_package_id",
    "jurisdiction_package_version", "mandate_ref",
    "mandate_version", "permitted_operations", "prohibited_operations",
    "decision_boundaries", "policy_pack_version", "source_pack_version",
    "authority_profile_version", "created_at",
}
FORBIDDEN_KEYS = {
    "api_key", "access_token", "refresh_token", "client_secret", "password",
    "private_key", "secret", "credential", "model_binary", "weights",
}
ROLE_BY_ACTION = {
    "REGISTER": "minah_deployment_registrar",
    "REVISE": "minah_deployment_configurator",
    "ACTIVE": "minah_deployment_approver",
    "SUSPENDED": "minah_deployment_suspension_authority",
    "REVOKED": "minah_deployment_revocation_authority",
}
TRANSITIONS = {
    "PENDING": {"ACTIVE", "SUSPENDED", "REVOKED"},
    "ACTIVE": {"SUSPENDED", "REVOKED"},
    "SUSPENDED": {"ACTIVE", "REVOKED"},
    "REVOKED": set(),
}


class MinahDeploymentError(ValueError):
    """Invalid MINAH deployment request or unavailable resource."""


class MinahDeploymentConflict(MinahDeploymentError):
    """Stale version, duplicate resource, or immutable-version conflict."""


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise MinahDeploymentError("MINAH_DEPLOYMENT_" + field.upper() + "_INVALID")
    return value


def _model_reference(value: Any) -> str:
    """Validate a namespaced model reference, which may contain URI path separators."""
    if (not isinstance(value, str) or len(value) > 256
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", value)):
        raise MinahDeploymentError("MINAH_MODEL_REFERENCE_INVALID")
    return value


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_CANONICAL") from exc


def _serialized_registry_method(method):
    """Serialize use of the registry's shared SQLite connection per process."""
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


def _validate(config: Any, tenant_id: str) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise MinahDeploymentError("MINAH_DEPLOYMENT_CONFIG_MUST_BE_OBJECT")
    if set(config) != FIELDS:
        missing = sorted(FIELDS - set(config))
        unknown = sorted(set(config) - FIELDS)
        raise MinahDeploymentError(
            "MINAH_DEPLOYMENT_FIELDS_INVALID:missing=" + ",".join(missing)
            + ";unknown=" + ",".join(unknown)
        )
    for key in config:
        if key.lower() in FORBIDDEN_KEYS:
            raise MinahDeploymentError("MINAH_SECRET_OR_MODEL_MATERIAL_FORBIDDEN")
    for field in ("deployment_id", "version", "agent_ref", "interface_version",
                  "owner_ref", "identity_provider_id",
                  "identity_provider_version", "jurisdiction_package_id",
                  "jurisdiction_package_version", "mandate_ref", "mandate_version",
                  "policy_pack_version", "source_pack_version",
                  "authority_profile_version"):
        _identifier(config[field], field)
    _model_reference(config["model_ref"])
    if config["status"] != "PENDING":
        raise MinahDeploymentError("NEW_OR_REVISED_DEPLOYMENT_MUST_BE_PENDING")
    if not isinstance(config["agent_kind"], str) or config["agent_kind"] not in AGENT_KINDS:
        raise MinahDeploymentError("MINAH_AGENT_KIND_UNSUPPORTED")
    if not isinstance(config["deployment_mode"], str) or config["deployment_mode"] not in DEPLOYMENT_MODES:
        raise MinahDeploymentError("MINAH_DEPLOYMENT_MODE_UNSUPPORTED")
    if (not isinstance(config["owner_scope"], str)
            or config["owner_scope"] not in {"NATIONAL_SOVEREIGN", "ENTERPRISE", "INSTITUTION"}):
        raise MinahDeploymentError("MINAH_OWNER_SCOPE_INVALID")
    if not isinstance(config["jurisdictions"], list) or not config["jurisdictions"]:
        raise MinahDeploymentError("MINAH_JURISDICTIONS_REQUIRED")
    if any(not isinstance(x, str) or not x.strip() or len(x) > 160
           for x in config["jurisdictions"]) or len(set(config["jurisdictions"])) != len(config["jurisdictions"]):
        raise MinahDeploymentError("MINAH_JURISDICTIONS_INVALID")
    for field in ("permitted_operations", "prohibited_operations", "decision_boundaries"):
        values = config[field]
        if (not isinstance(values, list) or not values
                or any(not isinstance(x, str) or not x.strip() or len(x) > 240 for x in values)
                or len(set(values)) != len(values)):
            raise MinahDeploymentError("MINAH_" + field.upper() + "_INVALID")
    if set(config["permitted_operations"]) & set(config["prohibited_operations"]):
        raise MinahDeploymentError("MINAH_OPERATION_ALLOW_DENY_CONFLICT")
    if "authority.grant" not in config["prohibited_operations"]:
        raise MinahDeploymentError("MINAH_MUST_NOT_GRANT_INSTITUTIONAL_AUTHORITY")
    if "bypass.cee" not in config["prohibited_operations"]:
        raise MinahDeploymentError("MINAH_MUST_NOT_BYPASS_CEE")
    if "CEE_FINAL_ADMISSION" not in config["decision_boundaries"]:
        raise MinahDeploymentError("CEE_FINAL_ADMISSION_BOUNDARY_REQUIRED")
    for field in ("endpoint_ref", "secret_ref"):
        value = config[field]
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 512):
            raise MinahDeploymentError("MINAH_" + field.upper() + "_INVALID")
    if config["agent_kind"] == "THIRD_PARTY_HOSTED" and not config["endpoint_ref"]:
        raise MinahDeploymentError("HOSTED_MINAH_ENDPOINT_REFERENCE_REQUIRED")
    if config["secret_ref"] and not config["secret_ref"].startswith("secret://"):
        raise MinahDeploymentError("MINAH_SECRET_REFERENCE_REQUIRED")
    if config["owner_ref"] != tenant_id:
        raise MinahDeploymentError("MINAH_OWNER_TENANT_MISMATCH")
    if not isinstance(config["created_at"], str) or not config["created_at"].strip():
        raise MinahDeploymentError("MINAH_CREATED_AT_REQUIRED")
    try:
        parsed = datetime.fromisoformat(config["created_at"].replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
    except ValueError as exc:
        raise MinahDeploymentError("MINAH_CREATED_AT_MUST_BE_TIMEZONE_AWARE") from exc
    return dict(config)


class InstitutionalMinahDeploymentRegistry:
    """SQLite append-only version store with tenant-local current deployment head."""

    def __init__(self, database: str):
        self._lock = threading.RLock()
        self.db = sqlite3.connect(
            database, timeout=10.0, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS minah_deployment_versions (
          tenant_id TEXT NOT NULL, deployment_id TEXT NOT NULL, version TEXT NOT NULL,
          status TEXT NOT NULL, config_json TEXT NOT NULL, digest TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, deployment_id, version)
        );
        CREATE TABLE IF NOT EXISTS minah_deployment_heads (
          tenant_id TEXT NOT NULL, deployment_id TEXT NOT NULL, current_version TEXT NOT NULL,
          PRIMARY KEY (tenant_id, deployment_id),
          FOREIGN KEY (tenant_id, deployment_id, current_version)
            REFERENCES minah_deployment_versions(tenant_id, deployment_id, version)
        );
        CREATE TABLE IF NOT EXISTS minah_deployment_epochs (
          tenant_id TEXT NOT NULL, deployment_id TEXT NOT NULL, epoch INTEGER NOT NULL,
          PRIMARY KEY (tenant_id, deployment_id),
          FOREIGN KEY (tenant_id, deployment_id)
            REFERENCES minah_deployment_heads(tenant_id, deployment_id)
        );
        CREATE TABLE IF NOT EXISTS minah_execution_leases (
          lease_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, deployment_id TEXT NOT NULL,
          epoch INTEGER NOT NULL, token_digest TEXT NOT NULL, expires_at TEXT NOT NULL,
          released_at TEXT
        );
        CREATE INDEX IF NOT EXISTS minah_execution_leases_scope
          ON minah_execution_leases(tenant_id, deployment_id, epoch, expires_at);
        CREATE TABLE IF NOT EXISTS minah_deployment_events (
          tenant_id TEXT NOT NULL, event_id TEXT NOT NULL, deployment_id TEXT NOT NULL,
          version TEXT NOT NULL, event_type TEXT NOT NULL, actor_ref TEXT NOT NULL,
          evidence_ref TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY (tenant_id, event_id),
          FOREIGN KEY (tenant_id, deployment_id, version)
            REFERENCES minah_deployment_versions(tenant_id, deployment_id, version)
        );
        """)
        # Backfill epoch 1 for deployments created before lease fencing existed.
        self.db.execute("""INSERT OR IGNORE INTO minah_deployment_epochs
            (tenant_id,deployment_id,epoch)
            SELECT tenant_id,deployment_id,1 FROM minah_deployment_heads""")

    @_serialized_registry_method
    def close(self) -> None:
        self.db.close()

    def _event_fields(self, tenant_id: str, event_id: str, actor_ref: str,
                      evidence_ref: str) -> None:
        _identifier(tenant_id, "tenant_id")
        _identifier(event_id, "event_id")
        _identifier(actor_ref, "actor_ref")
        _identifier(evidence_ref, "evidence_ref")

    @_serialized_registry_method
    def register(self, *, tenant_id: str, config: dict[str, Any], event_id: str,
                 actor_ref: str, evidence_ref: str) -> dict[str, Any]:
        self._event_fields(tenant_id, event_id, actor_ref, evidence_ref)
        clean = _validate(config, tenant_id)
        body = _canonical(clean)
        digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute(
                "SELECT 1 FROM minah_deployment_heads WHERE tenant_id=? AND deployment_id=?",
                (tenant_id, clean["deployment_id"])).fetchone():
                raise MinahDeploymentConflict("MINAH_DEPLOYMENT_ALREADY_REGISTERED")
            self.db.execute("INSERT INTO minah_deployment_versions VALUES(?,?,?,?,?,?,?)",
                (tenant_id, clean["deployment_id"], clean["version"], "PENDING", body, digest, now))
            self.db.execute("INSERT INTO minah_deployment_heads VALUES(?,?,?)",
                (tenant_id, clean["deployment_id"], clean["version"]))
            self.db.execute("INSERT INTO minah_deployment_epochs VALUES(?,?,?)",
                (tenant_id, clean["deployment_id"], 1))
            self.db.execute("INSERT INTO minah_deployment_events VALUES(?,?,?,?,?,?,?,?,?)",
                (tenant_id, event_id, clean["deployment_id"], clean["version"],
                 "MINAH_DEPLOYMENT_REGISTERED", actor_ref, evidence_ref,
                 _canonical({"digest": digest}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"deployment_id": clean["deployment_id"], "version": clean["version"],
                "status": "PENDING", "digest": digest}

    @_serialized_registry_method
    def revise(self, *, tenant_id: str, deployment_id: str, expected_version: str,
               config: dict[str, Any], event_id: str, actor_ref: str,
               evidence_ref: str) -> dict[str, Any]:
        self._event_fields(tenant_id, event_id, actor_ref, evidence_ref)
        _identifier(deployment_id, "deployment_id")
        _identifier(expected_version, "expected_version")
        clean = _validate(config, tenant_id)
        if clean["deployment_id"] != deployment_id or clean["version"] == expected_version:
            raise MinahDeploymentError("MINAH_REVISION_SCOPE_INVALID")
        body = _canonical(clean)
        digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            head = self.db.execute("SELECT current_version FROM minah_deployment_heads WHERE tenant_id=? AND deployment_id=?",
                                   (tenant_id, deployment_id)).fetchone()
            if head is None:
                raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_FOUND")
            if head["current_version"] != expected_version:
                raise MinahDeploymentConflict("STALE_MINAH_DEPLOYMENT_VERSION")
            if self.db.execute("SELECT 1 FROM minah_deployment_versions WHERE tenant_id=? AND deployment_id=? AND version=?",
                               (tenant_id, deployment_id, clean["version"])).fetchone():
                raise MinahDeploymentConflict("MINAH_DEPLOYMENT_VERSION_EXISTS")
            self.db.execute("INSERT INTO minah_deployment_versions VALUES(?,?,?,?,?,?,?)",
                (tenant_id, deployment_id, clean["version"], "PENDING", body, digest, now))
            self.db.execute("UPDATE minah_deployment_heads SET current_version=? WHERE tenant_id=? AND deployment_id=? AND current_version=?",
                (clean["version"], tenant_id, deployment_id, expected_version))
            self.db.execute("UPDATE minah_deployment_epochs SET epoch=epoch+1 WHERE tenant_id=? AND deployment_id=?",
                (tenant_id, deployment_id))
            self.db.execute("INSERT INTO minah_deployment_events VALUES(?,?,?,?,?,?,?,?,?)",
                (tenant_id, event_id, deployment_id, clean["version"],
                 "MINAH_DEPLOYMENT_REVISED", actor_ref, evidence_ref,
                 _canonical({"from_version": expected_version, "to_version": clean["version"], "digest": digest}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"deployment_id": deployment_id, "version": clean["version"],
                "status": "PENDING", "digest": digest}

    @_serialized_registry_method
    def transition(self, *, tenant_id: str, deployment_id: str, expected_version: str,
                   new_version: str, new_status: str, actor_ref: str,
                   event_id: str, evidence_ref: str) -> dict[str, Any]:
        self._event_fields(tenant_id, event_id, actor_ref, evidence_ref)
        _identifier(deployment_id, "deployment_id")
        _identifier(expected_version, "expected_version")
        _identifier(new_version, "new_version")
        if not isinstance(new_status, str) or new_status not in STATUSES or new_version == expected_version:
            raise MinahDeploymentError("MINAH_TRANSITION_INVALID")
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            head = self.db.execute("SELECT current_version FROM minah_deployment_heads WHERE tenant_id=? AND deployment_id=?",
                                   (tenant_id, deployment_id)).fetchone()
            if head is None:
                raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_FOUND")
            if head["current_version"] != expected_version:
                raise MinahDeploymentConflict("STALE_MINAH_DEPLOYMENT_VERSION")
            row = self.db.execute("SELECT config_json,status FROM minah_deployment_versions WHERE tenant_id=? AND deployment_id=? AND version=?",
                                  (tenant_id, deployment_id, expected_version)).fetchone()
            if new_status not in TRANSITIONS[row["status"]]:
                raise MinahDeploymentError("MINAH_DEPLOYMENT_TRANSITION_NOT_ALLOWED")
            if self.db.execute("SELECT 1 FROM minah_deployment_versions WHERE tenant_id=? AND deployment_id=? AND version=?",
                               (tenant_id, deployment_id, new_version)).fetchone():
                raise MinahDeploymentConflict("MINAH_DEPLOYMENT_VERSION_EXISTS")
            snapshot = json.loads(row["config_json"])
            snapshot["version"], snapshot["status"] = new_version, new_status
            body = _canonical(snapshot)
            digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
            self.db.execute("INSERT INTO minah_deployment_versions VALUES(?,?,?,?,?,?,?)",
                (tenant_id, deployment_id, new_version, new_status, body, digest, now))
            self.db.execute("UPDATE minah_deployment_heads SET current_version=? WHERE tenant_id=? AND deployment_id=? AND current_version=?",
                (new_version, tenant_id, deployment_id, expected_version))
            # Every lifecycle transition fences all leases issued under the prior epoch.
            self.db.execute("UPDATE minah_deployment_epochs SET epoch=epoch+1 WHERE tenant_id=? AND deployment_id=?",
                (tenant_id, deployment_id))
            self.db.execute("INSERT INTO minah_deployment_events VALUES(?,?,?,?,?,?,?,?,?)",
                (tenant_id, event_id, deployment_id, new_version, "MINAH_DEPLOYMENT_" + new_status,
                 actor_ref, evidence_ref,
                 _canonical({"from_status": row["status"], "to_status": new_status,
                             "expected_version": expected_version, "digest": digest}), now))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"deployment_id": deployment_id, "version": new_version,
                "status": new_status, "digest": digest}

    @_serialized_registry_method
    def get(self, *, tenant_id: str, deployment_id: str) -> dict[str, Any] | None:
        _identifier(tenant_id, "tenant_id")
        _identifier(deployment_id, "deployment_id")
        row = self.db.execute("""SELECT v.version,v.status,v.config_json,v.digest,v.created_at,
                COALESCE(e.epoch,1) AS execution_epoch
            FROM minah_deployment_heads h JOIN minah_deployment_versions v
            ON v.tenant_id=h.tenant_id AND v.deployment_id=h.deployment_id AND v.version=h.current_version
            LEFT JOIN minah_deployment_epochs e
              ON e.tenant_id=h.tenant_id AND e.deployment_id=h.deployment_id
            WHERE h.tenant_id=? AND h.deployment_id=?""", (tenant_id, deployment_id)).fetchone()
        if row is None:
            return None
        return {"tenant_id": tenant_id, "deployment_id": deployment_id,
                "version": row["version"], "status": row["status"],
                "config": json.loads(row["config_json"]), "digest": row["digest"],
                "created_at": row["created_at"],
                "execution_epoch": row["execution_epoch"]}

    @_serialized_registry_method
    def acquire_execution_lease(self, *, tenant_id: str, deployment_id: str,
                                ttl_seconds: int = 30) -> dict[str, Any]:
        """Atomically lease the current ACTIVE deployment epoch for execution."""
        _identifier(tenant_id, "tenant_id")
        _identifier(deployment_id, "deployment_id")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 300:
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_TTL_INVALID")
        token = secrets.token_urlsafe(32)
        lease_id = secrets.token_urlsafe(18)
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("""SELECT v.status,v.version,COALESCE(e.epoch,1) AS epoch
                FROM minah_deployment_heads h JOIN minah_deployment_versions v
                  ON v.tenant_id=h.tenant_id AND v.deployment_id=h.deployment_id
                 AND v.version=h.current_version
                LEFT JOIN minah_deployment_epochs e
                  ON e.tenant_id=h.tenant_id AND e.deployment_id=h.deployment_id
                WHERE h.tenant_id=? AND h.deployment_id=?""",
                (tenant_id, deployment_id)).fetchone()
            if row is None:
                raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_FOUND")
            if row["status"] != "ACTIVE":
                raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_ACTIVE")
            epoch = int(row["epoch"])
            self.db.execute("""INSERT INTO minah_execution_leases
                (lease_id,tenant_id,deployment_id,epoch,token_digest,expires_at,released_at)
                VALUES(?,?,?,?,?,?,NULL)""",
                (lease_id, tenant_id, deployment_id, epoch,
                 hashlib.sha256(token.encode("utf-8")).hexdigest(), expires_at))
            self.db.execute("COMMIT")
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        return {"lease_id": lease_id, "lease_token": token,
                "tenant_id": tenant_id, "deployment_id": deployment_id,
                "execution_epoch": epoch, "version": row["version"], "expires_at": expires_at}

    @_serialized_registry_method
    def validate_execution_lease(self, *, lease: dict[str, Any]) -> dict[str, Any]:
        """Validate lease capability, expiry, ACTIVE state and current fencing epoch."""
        if not isinstance(lease, dict):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_INVALID")
        tenant_id, deployment_id = lease.get("tenant_id"), lease.get("deployment_id")
        lease_id, token = lease.get("lease_id"), lease.get("lease_token")
        if not all(isinstance(x, str) and x for x in
                   (tenant_id, deployment_id, lease_id, token)):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_INVALID")
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        row = self.db.execute("""SELECT l.epoch,l.token_digest,l.expires_at,l.released_at,
                v.status,COALESCE(e.epoch,1) AS current_epoch
            FROM minah_execution_leases l
            JOIN minah_deployment_heads h
              ON h.tenant_id=l.tenant_id AND h.deployment_id=l.deployment_id
            JOIN minah_deployment_versions v
              ON v.tenant_id=h.tenant_id AND v.deployment_id=h.deployment_id
             AND v.version=h.current_version
            LEFT JOIN minah_deployment_epochs e
              ON e.tenant_id=h.tenant_id AND e.deployment_id=h.deployment_id
            WHERE l.lease_id=? AND l.tenant_id=? AND l.deployment_id=?""",
            (lease_id, tenant_id, deployment_id)).fetchone()
        if row is None or not secrets.compare_digest(row["token_digest"], token_digest):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_INVALID")
        if row["released_at"] is not None:
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_RELEASED")
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_EXPIRED")
        if row["status"] != "ACTIVE":
            raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_ACTIVE")
        if int(row["epoch"]) != int(row["current_epoch"]):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_FENCED")
        if lease.get("execution_epoch") != int(row["epoch"]):
            raise MinahDeploymentError("MINAH_EXECUTION_LEASE_EPOCH_MISMATCH")
        return {"lease_id": lease_id, "execution_epoch": int(row["epoch"]),
                "status": "VALID", "expires_at": row["expires_at"]}

    @_serialized_registry_method
    def execute_under_execution_lease(self, *, lease: dict[str, Any],
                                      action: Any) -> Any:
        """Serialize a protected commit against concurrent lifecycle transitions."""
        if not callable(action):
            raise MinahDeploymentError("MINAH_EXECUTION_ACTION_INVALID")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.validate_execution_lease(lease=lease)
            result = action()
            self.db.execute("COMMIT")
            return result
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    @_serialized_registry_method
    def release_execution_lease(self, *, lease: dict[str, Any]) -> None:
        """Idempotently release a lease without restoring a fenced epoch."""
        if not isinstance(lease, dict):
            return
        lease_id, token = lease.get("lease_id"), lease.get("lease_token")
        if not isinstance(lease_id, str) or not isinstance(token, str):
            return
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.db.execute("""UPDATE minah_execution_leases SET released_at=?
            WHERE lease_id=? AND token_digest=? AND released_at IS NULL""",
            (datetime.now(timezone.utc).isoformat(), lease_id, digest))

    @_serialized_registry_method
    def list_events(self, *, tenant_id: str, deployment_id: str) -> list[dict[str, Any]]:
        _identifier(tenant_id, "tenant_id")
        _identifier(deployment_id, "deployment_id")
        rows = self.db.execute("""SELECT event_id,version,event_type,actor_ref,evidence_ref,payload_json,created_at
            FROM minah_deployment_events WHERE tenant_id=? AND deployment_id=?
            ORDER BY created_at,rowid""", (tenant_id, deployment_id)).fetchall()
        return [{"event_id": r["event_id"], "version": r["version"],
                 "event_type": r["event_type"], "actor_ref": r["actor_ref"],
                 "evidence_ref": r["evidence_ref"], "payload": json.loads(r["payload_json"]),
                 "created_at": r["created_at"]} for r in rows]


class InstitutionalMinahDeploymentAdministration:
    """Authority-gated API facade; session tenant and actor never come from payload."""

    def __init__(self, *, registry: InstitutionalMinahDeploymentRegistry,
                 authority_resolver: Any, identity_provider_registry: Any):
        if not callable(getattr(authority_resolver, "resolve", None)):
            raise MinahDeploymentError("MINAH_AUTHORITY_RESOLVER_REQUIRED")
        if not callable(getattr(identity_provider_registry, "resolve_active_provider", None)):
            raise MinahDeploymentError("MINAH_ACTIVE_IDENTITY_PROVIDER_RESOLVER_REQUIRED")
        self.registry, self.authority, self.providers = registry, authority_resolver, identity_provider_registry

    def _authorise(self, assertion: dict[str, Any], *, tenant_id: str,
                   deployment_id: str, action: str) -> str:
        try:
            principal = self.authority.resolve(assertion, tenant_id=tenant_id,
                resource_type="minah_deployment", resource_id=deployment_id)
        except Exception as exc:
            raise MinahDeploymentError("MINAH_DEPLOYMENT_AUTHORITY_REJECTED") from exc
        if (not isinstance(principal, dict) or principal.get("authenticated") is not True
                or not isinstance(principal.get("actor_ref"), str)
                or not principal["actor_ref"].strip()
                or not isinstance(principal.get("roles"), list)
                or ROLE_BY_ACTION[action] not in principal["roles"]):
            raise MinahDeploymentError("MINAH_DEPLOYMENT_AUTHORITY_INSUFFICIENT")
        return principal["actor_ref"]

    def _check_provider(self, tenant_id: str, config: dict[str, Any]) -> None:
        resolver = getattr(self.providers, "resolve_active_provider", None)
        provider = (resolver(tenant_id, config["identity_provider_id"])
                    if callable(resolver) else None)
        if (provider is None or provider.get("status") != "ACTIVE"
                or provider.get("version") != config["identity_provider_version"]):
            raise MinahDeploymentError("MINAH_IDENTITY_PROVIDER_NOT_ACTIVE_OR_VERSION_MISMATCH")

    def register(self, *, tenant_id: str, config: dict[str, Any],
                 actor_assertion: dict[str, Any], event_id: str,
                 evidence_ref: str) -> dict[str, Any]:
        _identifier(tenant_id, "tenant_id")
        clean = _validate(config, tenant_id)
        self._check_provider(tenant_id, clean)
        actor = self._authorise(actor_assertion, tenant_id=tenant_id,
            deployment_id=clean["deployment_id"], action="REGISTER")
        return self.registry.register(tenant_id=tenant_id, config=clean,
            event_id=event_id, actor_ref=actor, evidence_ref=evidence_ref)

    def revise(self, *, tenant_id: str, deployment_id: str, expected_version: str,
               config: dict[str, Any], actor_assertion: dict[str, Any],
               event_id: str, evidence_ref: str) -> dict[str, Any]:
        _identifier(tenant_id, "tenant_id")
        _identifier(deployment_id, "deployment_id")
        _identifier(expected_version, "expected_version")
        clean = _validate(config, tenant_id)
        self._check_provider(tenant_id, clean)
        actor = self._authorise(actor_assertion, tenant_id=tenant_id,
            deployment_id=deployment_id, action="REVISE")
        return self.registry.revise(tenant_id=tenant_id, deployment_id=deployment_id,
            expected_version=expected_version, config=clean, event_id=event_id,
            actor_ref=actor, evidence_ref=evidence_ref)

    def transition(self, *, tenant_id: str, deployment_id: str,
                   expected_version: str, new_version: str, new_status: str,
                   actor_assertion: dict[str, Any], event_id: str,
                   evidence_ref: str) -> dict[str, Any]:
        _identifier(tenant_id, "tenant_id")
        _identifier(deployment_id, "deployment_id")
        if not isinstance(new_status, str) or new_status not in ROLE_BY_ACTION or new_status == "REGISTER":
            raise MinahDeploymentError("MINAH_DEPLOYMENT_STATUS_UNSUPPORTED")
        actor = self._authorise(actor_assertion, tenant_id=tenant_id,
            deployment_id=deployment_id, action=new_status)
        if new_status == "ACTIVE":
            current = self.registry.get(tenant_id=tenant_id, deployment_id=deployment_id)
            if current is None:
                raise MinahDeploymentError("MINAH_DEPLOYMENT_NOT_FOUND")
            self._check_provider(tenant_id, current["config"])
            if not all(current["config"][field] for field in (
                "mandate_ref", "mandate_version", "permitted_operations",
                "prohibited_operations", "decision_boundaries", "policy_pack_version",
                "source_pack_version", "authority_profile_version")):
                raise MinahDeploymentError("MINAH_GOVERNANCE_BINDINGS_INCOMPLETE")
        return self.registry.transition(tenant_id=tenant_id,
            deployment_id=deployment_id, expected_version=expected_version,
            new_version=new_version, new_status=new_status, actor_ref=actor,
            event_id=event_id, evidence_ref=evidence_ref)

    def get(self, *, tenant_id: str, deployment_id: str) -> dict[str, Any] | None:
        return self.registry.get(tenant_id=tenant_id, deployment_id=deployment_id)

    def resolve_active(self, *, tenant_id: str, deployment_id: str) -> dict[str, Any] | None:
        """Return an executable deployment only while its exact IdP pin is active.

        Runtime callers must use this resolver before accepting an instruction;
        a stale or suspended identity provider disables the deployment.
        """
        deployment = self.registry.get(tenant_id=tenant_id, deployment_id=deployment_id)
        if deployment is None or deployment["status"] != "ACTIVE":
            return None
        try:
            self._check_provider(tenant_id, deployment["config"])
        except MinahDeploymentError:
            return None
        return deployment

    def acquire_execution_lease(self, *, tenant_id: str, deployment_id: str,
                                ttl_seconds: int = 30) -> dict[str, Any]:
        """Acquire a tenant-scoped execution capability for the current ACTIVE epoch."""
        return self.registry.acquire_execution_lease(
            tenant_id=tenant_id, deployment_id=deployment_id,
            ttl_seconds=ttl_seconds)

    def validate_execution_lease(self, *, lease: dict[str, Any]) -> dict[str, Any]:
        return self.registry.validate_execution_lease(lease=lease)

    def execute_under_execution_lease(self, *, lease: dict[str, Any],
                                      action: Any) -> Any:
        return self.registry.execute_under_execution_lease(
            lease=lease, action=action)

    def release_execution_lease(self, *, lease: dict[str, Any]) -> None:
        self.registry.release_execution_lease(lease=lease)

    def list_events(self, *, tenant_id: str, deployment_id: str) -> list[dict[str, Any]]:
        return self.registry.list_events(tenant_id=tenant_id, deployment_id=deployment_id)
