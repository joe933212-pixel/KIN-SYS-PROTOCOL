# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""PostgreSQL reference backend for institutional authority grants and trust roots.

The caller must derive tenant IDs from authenticated request context. Public
keys remain in an external trust/key service; this database stores references,
not private key material. This adapter implements the VerifiedAuthorityGrantStore
contract and revalidates retained issuer and receiver provenance on every load.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable

from institutional_trust_registry import TrustRegistryConflict, TrustRegistryError

GRANT_FIELDS = {
    "grant_id", "tenant_id", "status", "actor_ref", "institution_id",
    "authority_ref", "roles", "resource_types", "resource_ids",
    "valid_from", "valid_until", "version", "issuer_institution_id",
    "approval_ref",
}
GRANT_STATUSES = {"PENDING", "ACTIVE", "SUSPENDED", "REVOKED"}
KEY_ALGORITHMS = {"Ed25519"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TrustRegistryError("VALUE_NOT_CANONICALISABLE") from exc


def _object(value: Any, code: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise TrustRegistryError(code) from exc
        if isinstance(decoded, dict):
            return decoded
    raise TrustRegistryError(code)


def _identifier(value: Any, code: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise TrustRegistryError(code)
    return value


def _timestamp(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _event_id(prefix: str, tenant_id: str, resource_id: str) -> str:
    digest = hashlib.sha256((tenant_id + ":" + resource_id).encode("utf-8")).hexdigest()
    return prefix + "-" + digest[:40]


class PostgreSQLInstitutionalTrustRegistry:
    """PostgreSQL-backed tenant-scoped authority and issuer-trust registry.

    Each operation obtains and closes a fresh psycopg 3 connection. The
    connection factory must return an idle connection. Transactions lock the
    current grant/issuer heads before mutation. Immutable snapshots, provenance
    and audit rows are also protected by database triggers in migration 005.
    """

    def __init__(self, connection_factory: Callable[[], Any]):
        if not callable(connection_factory):
            raise TrustRegistryError("POSTGRES_CONNECTION_FACTORY_REQUIRED")
        self._connect = connection_factory

    @staticmethod
    def _tenant(tenant_id: str) -> str:
        return _identifier(tenant_id, "TENANT_CONTEXT_REQUIRED")

    @staticmethod
    def _validate_grant(grant: dict[str, Any]) -> None:
        if not isinstance(grant, dict) or set(grant) != GRANT_FIELDS:
            raise TrustRegistryError("AUTHORITY_GRANT_FIELDS_INVALID")
        for field in (
            "grant_id", "tenant_id", "actor_ref", "institution_id",
            "authority_ref", "version", "issuer_institution_id", "approval_ref",
        ):
            _identifier(grant[field], "INVALID_GRANT_" + field.upper())
        if grant["status"] not in GRANT_STATUSES:
            raise TrustRegistryError("INVALID_GRANT_STATUS")
        if type(grant["valid_from"]) is not int or type(grant["valid_until"]) is not int:
            raise TrustRegistryError("INVALID_GRANT_VALIDITY")
        if grant["valid_until"] <= grant["valid_from"]:
            raise TrustRegistryError("INVALID_GRANT_VALIDITY")
        for field in ("roles", "resource_types", "resource_ids"):
            values = grant[field]
            if not isinstance(values, list) or not values or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise TrustRegistryError("INVALID_GRANT_SCOPE")

    @staticmethod
    def _event(
        cursor: Any, *, tenant_id: str, event_id: str, grant_id: str,
        version: str, event_type: str, actor_ref: str, evidence_ref: str,
        payload: dict[str, Any], created_at: datetime,
    ) -> None:
        cursor.execute(
            """INSERT INTO authority_grant_events
               (tenant_id,event_id,grant_id,version,event_type,actor_ref,
                evidence_ref,payload_json,created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
               ON CONFLICT DO NOTHING RETURNING event_id""",
            (tenant_id, event_id, grant_id, version, event_type, actor_ref,
             evidence_ref, _canonical(payload), created_at),
        )
        if cursor.fetchone() is None:
            raise TrustRegistryConflict("GRANT_EVENT_ID_ALREADY_EXISTS")

    @staticmethod
    def _insert_version(
        cursor: Any, *, tenant_id: str, grant: dict[str, Any], created_at: datetime,
    ) -> None:
        body = _canonical(grant)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        cursor.execute(
            """INSERT INTO authority_grant_versions
               (tenant_id,grant_id,version,status,payload_json,payload_sha256,created_at)
               VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s)
               ON CONFLICT DO NOTHING RETURNING version""",
            (tenant_id, grant["grant_id"], grant["version"], grant["status"],
             body, digest, created_at),
        )
        if cursor.fetchone() is None:
            raise TrustRegistryConflict("IMMUTABLE_GRANT_VERSION_CONFLICT")

    @staticmethod
    def _insert_revision_locked(
        cursor: Any, *, tenant_id: str, grant_id: str, expected_version: str,
        grant: dict[str, Any], event_id: str, actor_ref: str, evidence_ref: str,
        event_type: str, created_at: datetime,
    ) -> None:
        PostgreSQLInstitutionalTrustRegistry._validate_grant(grant)
        if grant["tenant_id"] != tenant_id or grant["grant_id"] != grant_id:
            raise TrustRegistryError("GRANT_TENANT_OR_ID_MISMATCH")
        _identifier(event_id, "INVALID_EVENT_ID")
        _identifier(actor_ref, "INVALID_EVENT_ACTOR")
        _identifier(evidence_ref, "GRANT_EVIDENCE_REQUIRED")
        _identifier(event_type, "INVALID_EVENT_TYPE")
        if grant["version"] == expected_version:
            raise TrustRegistryError("GRANT_REVISION_VERSION_MUST_ADVANCE")
        PostgreSQLInstitutionalTrustRegistry._insert_version(
            cursor, tenant_id=tenant_id, grant=grant, created_at=created_at,
        )
        cursor.execute(
            """UPDATE authority_grant_heads SET current_version=%s
               WHERE tenant_id=%s AND grant_id=%s AND current_version=%s""",
            (grant["version"], tenant_id, grant_id, expected_version),
        )
        if cursor.rowcount != 1:
            raise TrustRegistryConflict("STALE_GRANT_VERSION")
        PostgreSQLInstitutionalTrustRegistry._event(
            cursor, tenant_id=tenant_id, event_id=event_id, grant_id=grant_id,
            version=grant["version"], event_type=event_type, actor_ref=actor_ref,
            evidence_ref=evidence_ref,
            payload={"from_version": expected_version, "grant_digest_payload": grant},
            created_at=created_at,
        )

    def create_grant_version(
        self, *, tenant_id: str, grant: dict[str, Any], event_id: str,
        actor_ref: str, evidence_ref: str,
        event_type: str = "GRANT_VERSION_CREATED",
    ) -> dict[str, Any]:
        tenant = self._tenant(tenant_id)
        self._validate_grant(grant)
        if grant["tenant_id"] != tenant:
            raise TrustRegistryError("GRANT_TENANT_MISMATCH")
        for value, code in (
            (event_id, "INVALID_EVENT_ID"), (actor_ref, "INVALID_EVENT_ACTOR"),
            (evidence_ref, "GRANT_EVIDENCE_REQUIRED"), (event_type, "INVALID_EVENT_TYPE"),
        ):
            _identifier(value, code)
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (tenant + ":" + grant["grant_id"],),
                    )
                    cursor.execute(
                        """SELECT current_version FROM authority_grant_heads
                           WHERE tenant_id=%s AND grant_id=%s FOR UPDATE""",
                        (tenant, grant["grant_id"]),
                    )
                    if cursor.fetchone() is not None:
                        raise TrustRegistryConflict("GRANT_VERSION_HEAD_MUST_ADVANCE_EXPLICITLY")
                    self._insert_version(
                        cursor, tenant_id=tenant, grant=grant, created_at=now,
                    )
                    cursor.execute(
                        """INSERT INTO authority_grant_heads
                           (tenant_id,grant_id,current_version) VALUES (%s,%s,%s)""",
                        (tenant, grant["grant_id"], grant["version"]),
                    )
                    self._event(
                        cursor, tenant_id=tenant, event_id=event_id,
                        grant_id=grant["grant_id"], version=grant["version"],
                        event_type=event_type, actor_ref=actor_ref,
                        evidence_ref=evidence_ref,
                        payload={"grant_digest_payload": grant}, created_at=now,
                    )
        return {"created": True, "version": grant["version"]}

    def create_grant_revision(
        self, *, tenant_id: str, grant_id: str, expected_version: str,
        grant: dict[str, Any], event_id: str, actor_ref: str,
        evidence_ref: str, event_type: str = "GRANT_AMENDED",
    ) -> dict[str, Any]:
        tenant = self._tenant(tenant_id)
        _identifier(grant_id, "INVALID_GRANT_ID")
        _identifier(expected_version, "INVALID_EXPECTED_GRANT_VERSION")
        self._validate_grant(grant)
        if grant["tenant_id"] != tenant or grant["grant_id"] != grant_id:
            raise TrustRegistryError("GRANT_TENANT_OR_ID_MISMATCH")
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT current_version FROM authority_grant_heads
                           WHERE tenant_id=%s AND grant_id=%s FOR UPDATE""",
                        (tenant, grant_id),
                    )
                    head = cursor.fetchone()
                    if head is None:
                        raise TrustRegistryError("AUTHORITY_GRANT_NOT_FOUND")
                    if head[0] != expected_version:
                        raise TrustRegistryConflict("STALE_GRANT_VERSION")
                    self._insert_revision_locked(
                        cursor, tenant_id=tenant, grant_id=grant_id,
                        expected_version=expected_version, grant=grant,
                        event_id=event_id, actor_ref=actor_ref,
                        evidence_ref=evidence_ref, event_type=event_type,
                        created_at=now,
                    )
        return {"created": True, "version": grant["version"]}

    def revoke_grant(
        self, *, tenant_id: str, grant_id: str, expected_version: str,
        new_version: str, event_id: str, actor_ref: str, evidence_ref: str,
    ) -> dict[str, Any]:
        tenant = self._tenant(tenant_id)
        _identifier(grant_id, "INVALID_GRANT_ID")
        _identifier(expected_version, "INVALID_EXPECTED_GRANT_VERSION")
        _identifier(new_version, "INVALID_NEW_GRANT_VERSION")
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT h.current_version,v.payload_json
                           FROM authority_grant_heads h
                           JOIN authority_grant_versions v
                             ON v.tenant_id=h.tenant_id AND v.grant_id=h.grant_id
                            AND v.version=h.current_version
                           WHERE h.tenant_id=%s AND h.grant_id=%s FOR UPDATE OF h""",
                        (tenant, grant_id),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise TrustRegistryError("AUTHORITY_GRANT_NOT_FOUND")
                    if row[0] != expected_version:
                        raise TrustRegistryConflict("STALE_GRANT_VERSION")
                    current = _object(row[1], "AUTHORITY_GRANT_RECORD_INVALID")
                    if current.get("status") == "REVOKED":
                        raise TrustRegistryConflict("AUTHORITY_GRANT_ALREADY_REVOKED")
                    revoked = dict(
                        current, status="REVOKED", version=new_version,
                        approval_ref=evidence_ref,
                    )
                    self._insert_revision_locked(
                        cursor, tenant_id=tenant, grant_id=grant_id,
                        expected_version=expected_version, grant=revoked,
                        event_id=event_id, actor_ref=actor_ref,
                        evidence_ref=evidence_ref, event_type="GRANT_REVOKED",
                        created_at=now,
                    )
        return {"revoked": True, "version": new_version, "grant_id": grant_id}

    def load_grant(self, tenant_id: str, grant_id: str) -> dict[str, Any] | None:
        tenant = self._tenant(tenant_id)
        _identifier(grant_id, "INVALID_GRANT_ID")
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT v.payload_json FROM authority_grant_heads h
                       JOIN authority_grant_versions v
                         ON v.tenant_id=h.tenant_id AND v.grant_id=h.grant_id
                        AND v.version=h.current_version
                       WHERE h.tenant_id=%s AND h.grant_id=%s""",
                    (tenant, grant_id),
                )
                row = cursor.fetchone()
        return _object(row[0], "AUTHORITY_GRANT_RECORD_INVALID") if row else None

    def list_grant_events(self, *, tenant_id: str, grant_id: str) -> list[dict[str, Any]]:
        tenant = self._tenant(tenant_id)
        _identifier(grant_id, "INVALID_GRANT_ID")
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT event_id,version,event_type,actor_ref,evidence_ref,
                              payload_json,created_at
                       FROM authority_grant_events
                       WHERE tenant_id=%s AND grant_id=%s
                       ORDER BY created_at,event_id""",
                    (tenant, grant_id),
                )
                rows = cursor.fetchall()
        return [{
            "event_id": row[0], "version": row[1], "event_type": row[2],
            "actor_ref": row[3], "evidence_ref": row[4],
            "payload": _object(row[5], "GRANT_EVENT_PAYLOAD_INVALID"),
            "created_at": _timestamp(row[6]),
        } for row in rows]

    @staticmethod
    def _load_trust_key_cursor(
        cursor: Any, tenant_id: str, key_id: str, *, lock: bool = False,
    ) -> dict[str, Any] | None:
        query = """SELECT k.tenant_id,k.issuer_institution_id,k.key_id,k.algorithm,
                          k.public_key_ref,k.status,k.valid_from,k.valid_until,
                          i.valid_from AS issuer_valid_from,
                          i.valid_until AS issuer_valid_until
                   FROM institutional_trust_keys k
                   JOIN trusted_issuer_relationships i
                     ON i.tenant_id=k.tenant_id
                    AND i.issuer_institution_id=k.issuer_institution_id
                   WHERE k.tenant_id=%s AND k.key_id=%s AND i.status='ACTIVE'"""
        if lock:
            query += " FOR SHARE OF k, i"
        cursor.execute(query, (tenant_id, key_id))
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "tenant_id": row[0], "issuer_institution_id": row[1],
            "key_id": row[2], "algorithm": row[3], "public_key_ref": row[4],
            "status": row[5], "valid_from": int(row[6]), "valid_until": int(row[7]),
            "issuer_valid_from": int(row[8]), "issuer_valid_until": int(row[9]),
        }

    def load_trust_key(self, tenant_id: str, key_id: str) -> dict[str, Any] | None:
        tenant = self._tenant(tenant_id)
        _identifier(key_id, "INVALID_KEY_ID")
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                return self._load_trust_key_cursor(cursor, tenant, key_id)

    def get_trust_key(self, *, tenant_id: str, key_id: str) -> dict[str, Any] | None:
        tenant = self._tenant(tenant_id)
        _identifier(key_id, "INVALID_KEY_ID")
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT issuer_institution_id,key_id,algorithm,public_key_ref,
                              status,valid_from,valid_until,created_at
                       FROM institutional_trust_keys
                       WHERE tenant_id=%s AND key_id=%s""",
                    (tenant, key_id),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return {
            "issuer_institution_id": row[0], "key_id": row[1],
            "algorithm": row[2], "public_key_ref": row[3], "status": row[4],
            "valid_from": int(row[5]), "valid_until": int(row[6]),
            "created_at": _timestamp(row[7]),
        }

    def register_trusted_issuer(
        self, *, tenant_id: str, issuer_institution_id: str,
        trust_basis_ref: str, valid_from: int, valid_until: int,
        actor_ref: str, evidence_ref: str,
    ) -> dict[str, Any]:
        tenant = self._tenant(tenant_id)
        issuer = _identifier(issuer_institution_id, "INVALID_ISSUER_ID")
        _identifier(actor_ref, "INVALID_EVENT_ACTOR")
        _identifier(evidence_ref, "TRUST_EVIDENCE_REQUIRED")
        if not isinstance(trust_basis_ref, str) or not trust_basis_ref.strip():
            raise TrustRegistryError("TRUST_BASIS_REFERENCE_REQUIRED")
        if (type(valid_from) is not int or type(valid_until) is not int
                or valid_until <= valid_from):
            raise TrustRegistryError("INVALID_TRUST_RELATIONSHIP_VALIDITY")
        now = datetime.now(timezone.utc)
        event_id = _event_id("issuer-trust", tenant, issuer)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (tenant + ":issuer:" + issuer,),
                    )
                    cursor.execute(
                        """INSERT INTO trusted_issuer_relationships
                           (tenant_id,issuer_institution_id,status,trust_basis_ref,
                            valid_from,valid_until,created_at)
                           VALUES (%s,%s,'ACTIVE',%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING RETURNING issuer_institution_id""",
                        (tenant, issuer, trust_basis_ref, valid_from, valid_until, now),
                    )
                    if cursor.fetchone() is None:
                        raise TrustRegistryConflict("ISSUER_TRUST_RELATIONSHIP_ALREADY_EXISTS")
                    cursor.execute(
                        """INSERT INTO trusted_issuer_events
                           (tenant_id,event_id,issuer_institution_id,event_type,actor_ref,
                            evidence_ref,payload_json,created_at)
                           VALUES (%s,%s,%s,'ISSUER_TRUST_ESTABLISHED',%s,%s,%s::jsonb,%s)""",
                        (tenant, event_id, issuer, actor_ref, evidence_ref,
                         _canonical({"trust_basis_ref": trust_basis_ref}), now),
                    )
        return {"trusted": True, "issuer_institution_id": issuer}

    def revoke_trusted_issuer(
        self, *, tenant_id: str, issuer_institution_id: str,
        actor_ref: str, evidence_ref: str, event_id: str,
    ) -> dict[str, Any]:
        tenant = self._tenant(tenant_id)
        issuer = _identifier(issuer_institution_id, "INVALID_ISSUER_ID")
        _identifier(actor_ref, "INVALID_EVENT_ACTOR")
        _identifier(evidence_ref, "TRUST_EVIDENCE_REQUIRED")
        _identifier(event_id, "INVALID_EVENT_ID")
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """UPDATE trusted_issuer_relationships SET status='REVOKED'
                           WHERE tenant_id=%s AND issuer_institution_id=%s AND status='ACTIVE'
                           RETURNING issuer_institution_id""",
                        (tenant, issuer),
                    )
                    if cursor.fetchone() is None:
                        cursor.execute(
                            """SELECT status FROM trusted_issuer_relationships
                               WHERE tenant_id=%s AND issuer_institution_id=%s FOR UPDATE""",
                            (tenant, issuer),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            raise TrustRegistryError("TRUSTED_ISSUER_NOT_FOUND")
                        raise TrustRegistryConflict("ISSUER_TRUST_ALREADY_REVOKED")
                    cursor.execute(
                        """INSERT INTO trusted_issuer_events
                           (tenant_id,event_id,issuer_institution_id,event_type,actor_ref,
                            evidence_ref,payload_json,created_at)
                           VALUES (%s,%s,%s,'ISSUER_TRUST_REVOKED',%s,%s,%s::jsonb,%s)
                           ON CONFLICT DO NOTHING RETURNING event_id""",
                        (tenant, event_id, issuer, actor_ref, evidence_ref,
                         _canonical({"status": "REVOKED"}), now),
                    )
                    if cursor.fetchone() is None:
                        raise TrustRegistryConflict("TRUST_EVENT_ID_ALREADY_EXISTS")
        return {"revoked": True, "issuer_institution_id": issuer}

    def list_trusted_issuer_events(
        self, *, tenant_id: str, issuer_institution_id: str,
    ) -> list[dict[str, Any]]:
        tenant = self._tenant(tenant_id)
        issuer = _identifier(issuer_institution_id, "INVALID_ISSUER_ID")
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT event_id,event_type,actor_ref,evidence_ref,payload_json,created_at
                       FROM trusted_issuer_events
                       WHERE tenant_id=%s AND issuer_institution_id=%s
                       ORDER BY created_at,event_id""",
                    (tenant, issuer),
                )
                rows = cursor.fetchall()
        return [{
            "event_id": row[0], "event_type": row[1], "actor_ref": row[2],
            "evidence_ref": row[3],
            "payload": _object(row[4], "TRUST_EVENT_PAYLOAD_INVALID"),
            "created_at": _timestamp(row[5]),
        } for row in rows]

    def register_trust_key(
        self, *, tenant_id: str, issuer_institution_id: str, key_id: str,
        algorithm: str, public_key_ref: str, valid_from: int, valid_until: int,
        actor_ref: str, evidence_ref: str,
    ) -> dict[str, Any]:
        tenant = self._tenant(tenant_id)
        issuer = _identifier(issuer_institution_id, "INVALID_ISSUER_ID")
        key = _identifier(key_id, "INVALID_KEY_ID")
        _identifier(actor_ref, "INVALID_EVENT_ACTOR")
        _identifier(evidence_ref, "TRUST_EVIDENCE_REQUIRED")
        if (not isinstance(public_key_ref, str) or not public_key_ref.strip()
                or any(ord(char) < 32 for char in public_key_ref)):
            raise TrustRegistryError("PUBLIC_KEY_REFERENCE_REQUIRED")
        if algorithm not in KEY_ALGORITHMS:
            raise TrustRegistryError("UNSUPPORTED_TRUST_KEY_ALGORITHM")
        if (type(valid_from) is not int or type(valid_until) is not int
                or valid_until <= valid_from):
            raise TrustRegistryError("INVALID_TRUST_KEY_VALIDITY")
        now = datetime.now(timezone.utc)
        event_id = _event_id("key-register", tenant, key)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT status,valid_from,valid_until
                           FROM trusted_issuer_relationships
                           WHERE tenant_id=%s AND issuer_institution_id=%s FOR UPDATE""",
                        (tenant, issuer),
                    )
                    root = cursor.fetchone()
                    if root is None or root[0] != "ACTIVE":
                        raise TrustRegistryError("TRUSTED_ISSUER_NOT_ACTIVE")
                    if valid_from < int(root[1]) or valid_until > int(root[2]):
                        raise TrustRegistryError("TRUST_KEY_OUTSIDE_ISSUER_TRUST_WINDOW")
                    cursor.execute(
                        """INSERT INTO institutional_trust_keys
                           (tenant_id,issuer_institution_id,key_id,algorithm,public_key_ref,
                            status,valid_from,valid_until,created_at)
                           VALUES (%s,%s,%s,%s,%s,'ACTIVE',%s,%s,%s)
                           ON CONFLICT DO NOTHING RETURNING key_id""",
                        (tenant, issuer, key, algorithm, public_key_ref,
                         valid_from, valid_until, now),
                    )
                    if cursor.fetchone() is None:
                        raise TrustRegistryConflict("TRUST_KEY_ID_ALREADY_REGISTERED")
                    cursor.execute(
                        """INSERT INTO institutional_trust_key_events
                           (tenant_id,event_id,key_id,event_type,actor_ref,evidence_ref,
                            payload_json,created_at)
                           VALUES (%s,%s,%s,'TRUST_KEY_REGISTERED',%s,%s,%s::jsonb,%s)""",
                        (tenant, event_id, key, actor_ref, evidence_ref,
                         _canonical({
                             "issuer_institution_id": issuer,
                             "algorithm": algorithm, "public_key_ref": public_key_ref,
                             "valid_from": valid_from, "valid_until": valid_until,
                         }), now),
                    )
        return {"created": True, "key_id": key}

    def revoke_trust_key(
        self, *, tenant_id: str, key_id: str, actor_ref: str,
        evidence_ref: str, event_id: str,
    ) -> dict[str, Any]:
        tenant = self._tenant(tenant_id)
        key = _identifier(key_id, "INVALID_KEY_ID")
        _identifier(actor_ref, "INVALID_EVENT_ACTOR")
        _identifier(evidence_ref, "TRUST_EVIDENCE_REQUIRED")
        _identifier(event_id, "INVALID_EVENT_ID")
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """UPDATE institutional_trust_keys SET status='REVOKED'
                           WHERE tenant_id=%s AND key_id=%s AND status='ACTIVE'
                           RETURNING key_id""",
                        (tenant, key),
                    )
                    if cursor.fetchone() is None:
                        cursor.execute(
                            """SELECT status FROM institutional_trust_keys
                               WHERE tenant_id=%s AND key_id=%s FOR UPDATE""",
                            (tenant, key),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            raise TrustRegistryError("TRUST_KEY_NOT_FOUND")
                        raise TrustRegistryConflict("TRUST_KEY_ALREADY_REVOKED")
                    cursor.execute(
                        """INSERT INTO institutional_trust_key_events
                           (tenant_id,event_id,key_id,event_type,actor_ref,evidence_ref,
                            payload_json,created_at)
                           VALUES (%s,%s,%s,'TRUST_KEY_REVOKED',%s,%s,%s::jsonb,%s)
                           ON CONFLICT DO NOTHING RETURNING event_id""",
                        (tenant, event_id, key, actor_ref, evidence_ref,
                         _canonical({"status": "REVOKED"}), now),
                    )
                    if cursor.fetchone() is None:
                        raise TrustRegistryConflict("TRUST_KEY_EVENT_ID_ALREADY_EXISTS")
        return {"revoked": True, "key_id": key}

    def list_trust_key_events(self, *, tenant_id: str, key_id: str) -> list[dict[str, Any]]:
        tenant = self._tenant(tenant_id)
        key = _identifier(key_id, "INVALID_KEY_ID")
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT event_id,event_type,actor_ref,evidence_ref,payload_json,created_at
                       FROM institutional_trust_key_events
                       WHERE tenant_id=%s AND key_id=%s ORDER BY created_at,event_id""",
                    (tenant, key),
                )
                rows = cursor.fetchall()
        return [{
            "event_id": row[0], "event_type": row[1], "actor_ref": row[2],
            "evidence_ref": row[3],
            "payload": _object(row[4], "TRUST_EVENT_PAYLOAD_INVALID"),
            "created_at": _timestamp(row[5]),
        } for row in rows]

    def accept_signed_grant(
        self, *, tenant_id: str, envelope: dict[str, Any],
        receiver_institution_id: str, receiver_approval: dict[str, Any],
        verify_receiver_approval: Any, public_key_loader: Any,
        event_id: str, actor_ref: str, evidence_ref: str,
        receiver_signing_key_id: str, receiver_signing_key_loader: Any,
        clock: Any = None,
    ) -> dict[str, Any]:
        """Verify issuer grant, obtain receiver approval, then atomically persist.

        The receiver approval verifier and external private-key resolver are
        explicit trust boundaries. The receiver signer is not resolved until
        approval succeeds. Issuer trust is checked again under the persistence
        transaction so a concurrent trust revocation cannot race acceptance.
        """
        from institutional_grant_provenance import (
            accept_authority_grant, sign_receiver_acceptance_receipt,
            verify_authority_grant,
        )
        tenant = self._tenant(tenant_id)
        receiver = _identifier(receiver_institution_id, "INVALID_RECEIVER_INSTITUTION")
        for value, code in (
            (event_id, "INVALID_EVENT_ID"), (actor_ref, "INVALID_EVENT_ACTOR"),
            (evidence_ref, "GRANT_EVIDENCE_REQUIRED"),
            (receiver_signing_key_id, "RECEIVER_ACCEPTANCE_SIGNING_KEY_REQUIRED"),
        ):
            _identifier(value, code)
        if not isinstance(receiver_approval, dict):
            raise TrustRegistryError("RECEIVER_APPROVAL_REQUIRED")
        if not callable(receiver_signing_key_loader):
            raise TrustRegistryError("RECEIVER_ACCEPTANCE_SIGNER_REQUIRED")
        clock_fn = clock or time.time
        if not callable(clock_fn):
            raise TrustRegistryError("TRUST_REGISTRY_CLOCK_INVALID")
        now_value = clock_fn()
        if (isinstance(now_value, bool) or not isinstance(now_value, (int, float))
                or not math.isfinite(now_value)):
            raise TrustRegistryError("TRUST_REGISTRY_CLOCK_INVALID")
        accepted_at = int(now_value)
        if (receiver_approval.get("actor_ref") != actor_ref
                or receiver_approval.get("approval_ref") != evidence_ref):
            raise TrustRegistryError("RECEIVER_APPROVAL_AUDIT_CONTEXT_MISMATCH")
        try:
            accepted = accept_authority_grant(
                envelope, tenant_id=tenant, receiver_institution_id=receiver,
                trust_key_loader=self.load_trust_key,
                public_key_loader=public_key_loader,
                verify_receiver_approval=verify_receiver_approval,
                receiver_approval=receiver_approval,
                clock=lambda: now_value,
            )
        except Exception as exc:
            raise TrustRegistryError("SIGNED_GRANT_ACCEPTANCE_REJECTED:" + str(exc)) from exc
        try:
            receiver_private_key = receiver_signing_key_loader(
                tenant, receiver, receiver_signing_key_id,
            )
        except Exception as exc:
            raise TrustRegistryError("RECEIVER_ACCEPTANCE_SIGNER_UNAVAILABLE") from exc
        if not callable(getattr(receiver_private_key, "sign", None)):
            raise TrustRegistryError("RECEIVER_ACCEPTANCE_SIGNER_UNAVAILABLE")
        grant = accepted["grant"]
        receipt = sign_receiver_acceptance_receipt(
            tenant_id=tenant, receiver_institution_id=receiver,
            grant_id=grant["grant_id"], grant_version=grant["version"],
            grant_digest=accepted["acceptance"]["grant_digest"],
            receiver_approval=receiver_approval, accepted_at=accepted_at,
            key_id=receiver_signing_key_id, private_key=receiver_private_key,
        )
        accepted["acceptance"]["receiver_receipt"] = receipt
        self._validate_grant(grant)
        if grant["tenant_id"] != tenant or grant["status"] != "ACTIVE":
            raise TrustRegistryError("ACCEPTED_GRANT_SCOPE_INVALID")
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (tenant + ":" + grant["grant_id"],),
                    )
                    try:
                        issuer_grant = verify_authority_grant(
                            envelope, tenant_id=tenant,
                            trust_key_loader=lambda t, k: self._load_trust_key_cursor(
                                cursor, t, k, lock=True,
                            ),
                            public_key_loader=public_key_loader,
                            clock=lambda: now_value,
                        )
                    except Exception as exc:
                        raise TrustRegistryError(
                            "ISSUER_TRUST_CHANGED_DURING_ACCEPTANCE"
                        ) from exc
                    if dict(issuer_grant, status="ACTIVE") != grant:
                        raise TrustRegistryError("ACCEPTED_GRANT_SNAPSHOT_MISMATCH")
                    cursor.execute(
                        """SELECT current_version FROM authority_grant_heads
                           WHERE tenant_id=%s AND grant_id=%s FOR UPDATE""",
                        (tenant, grant["grant_id"]),
                    )
                    if cursor.fetchone() is not None:
                        raise TrustRegistryConflict("AUTHORITY_GRANT_ALREADY_EXISTS")
                    self._insert_version(
                        cursor, tenant_id=tenant, grant=grant, created_at=now,
                    )
                    cursor.execute(
                        """INSERT INTO authority_grant_heads
                           (tenant_id,grant_id,current_version) VALUES (%s,%s,%s)""",
                        (tenant, grant["grant_id"], grant["version"]),
                    )
                    digest = accepted["acceptance"]["grant_digest"]
                    cursor.execute(
                        """INSERT INTO accepted_grant_provenance
                           (tenant_id,grant_id,version,grant_digest,issuer_envelope_json,
                            acceptance_json,created_at)
                           VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)""",
                        (tenant, grant["grant_id"], grant["version"], digest,
                         _canonical(accepted["issuer_envelope"]),
                         _canonical(accepted["acceptance"]), now),
                    )
                    self._event(
                        cursor, tenant_id=tenant, event_id=event_id,
                        grant_id=grant["grant_id"], version=grant["version"],
                        event_type="FEDERATED_GRANT_ACCEPTED", actor_ref=actor_ref,
                        evidence_ref=evidence_ref,
                        payload={
                            "grant_digest": digest,
                            "issuer_envelope": accepted["issuer_envelope"],
                            "acceptance": accepted["acceptance"],
                        },
                        created_at=now,
                    )
        return {
            "accepted": True, "grant": grant,
            "grant_digest": accepted["acceptance"]["grant_digest"],
            "version": grant["version"], "receiver_receipt": receipt,
        }

    def load_grant_provenance(
        self, *, tenant_id: str, grant_id: str, version: str,
    ) -> dict[str, Any] | None:
        tenant = self._tenant(tenant_id)
        _identifier(grant_id, "INVALID_GRANT_ID")
        _identifier(version, "INVALID_GRANT_VERSION")
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT grant_digest,issuer_envelope_json,acceptance_json,created_at
                       FROM accepted_grant_provenance
                       WHERE tenant_id=%s AND grant_id=%s AND version=%s""",
                    (tenant, grant_id, version),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return {
            "grant_digest": row[0],
            "issuer_envelope": _object(row[1], "GRANT_PROVENANCE_ENVELOPE_INVALID"),
            "acceptance": _object(row[2], "GRANT_ACCEPTANCE_RECORD_INVALID"),
            "created_at": _timestamp(row[3]),
        }

    def load_verified_grant(
        self, tenant_id: str, grant_id: str, *,
        require_federated_provenance: bool,
        public_key_loader: Any,
        receiver_acceptance_key_loader: Any = None,
        receiver_acceptance_public_key_loader: Any = None,
        clock: Any = time.time,
    ) -> dict[str, Any] | None:
        """Implement VerifiedAuthorityGrantStore with transactionally pinned reads."""
        from institutional_grant_provenance import (
            canonical_grant_bytes, verify_authority_grant,
            verify_receiver_acceptance_receipt,
        )
        tenant = self._tenant(tenant_id)
        _identifier(grant_id, "INVALID_GRANT_ID")
        if type(require_federated_provenance) is not bool or not callable(clock):
            raise TrustRegistryError("VERIFIED_GRANT_REQUEST_INVALID")
        with closing(self._connect()) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    try:
                        cursor.execute(
                            """SELECT v.payload_json,v.payload_sha256 FROM authority_grant_heads h
                               JOIN authority_grant_versions v
                                 ON v.tenant_id=h.tenant_id AND v.grant_id=h.grant_id
                                AND v.version=h.current_version
                               WHERE h.tenant_id=%s AND h.grant_id=%s
                               FOR SHARE OF h""",
                            (tenant, grant_id),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            return None
                        grant = _object(row[0], "AUTHORITY_GRANT_RECORD_INVALID")
                        self._validate_grant(grant)
                        snapshot_digest = hashlib.sha256(_canonical(grant).encode("utf-8")).hexdigest()
                        if snapshot_digest != row[1]:
                            raise TrustRegistryError("GRANT_SNAPSHOT_DIGEST_MISMATCH")
                        if grant["status"] != "ACTIVE":
                            return grant
                        cursor.execute(
                            """SELECT grant_digest,issuer_envelope_json,acceptance_json
                               FROM accepted_grant_provenance
                               WHERE tenant_id=%s AND grant_id=%s AND version=%s""",
                            (tenant, grant_id, grant["version"]),
                        )
                        provenance = cursor.fetchone()
                        if provenance is None:
                            if require_federated_provenance:
                                raise TrustRegistryError("FEDERATED_GRANT_PROVENANCE_REQUIRED")
                            return grant
                        if not callable(public_key_loader):
                            raise TrustRegistryError("GRANT_PUBLIC_KEY_RESOLVER_REQUIRED")
                        envelope = _object(
                            provenance[1], "GRANT_PROVENANCE_ENVELOPE_INVALID",
                        )
                        acceptance = _object(
                            provenance[2], "GRANT_ACCEPTANCE_RECORD_INVALID",
                        )
                        try:
                            issuer_grant = verify_authority_grant(
                                envelope, tenant_id=tenant,
                                trust_key_loader=lambda t, k: self._load_trust_key_cursor(
                                    cursor, t, k, lock=True,
                                ),
                                public_key_loader=public_key_loader, clock=clock,
                            )
                        except Exception as exc:
                            raise TrustRegistryError(
                                "GRANT_PROVENANCE_VERIFICATION_FAILED"
                            ) from exc
                        if dict(issuer_grant, status="ACTIVE") != grant:
                            raise TrustRegistryError("GRANT_PROVENANCE_SNAPSHOT_MISMATCH")
                        digest = hashlib.sha256(
                            canonical_grant_bytes(issuer_grant)
                        ).hexdigest()
                        if provenance[0] != digest:
                            raise TrustRegistryError("GRANT_PROVENANCE_DIGEST_MISMATCH")
                        acceptance_fields = {
                            "receiver_institution_id", "grant_id", "grant_version",
                            "grant_digest", "receiver_approval", "receiver_receipt",
                        }
                        if set(acceptance) != acceptance_fields:
                            raise TrustRegistryError("GRANT_ACCEPTANCE_RECORD_INVALID")
                        approval = acceptance["receiver_approval"]
                        if not isinstance(approval, dict):
                            raise TrustRegistryError("GRANT_ACCEPTANCE_APPROVAL_INVALID")
                        approval_actor = _identifier(
                            approval.get("actor_ref"), "GRANT_ACCEPTANCE_ACTOR_INVALID",
                        )
                        approval_ref = _identifier(
                            approval.get("approval_ref"), "GRANT_ACCEPTANCE_EVIDENCE_INVALID",
                        )
                        if (
                            acceptance["receiver_institution_id"] != grant["institution_id"]
                            or acceptance["grant_id"] != grant["grant_id"]
                            or acceptance["grant_version"] != grant["version"]
                            or acceptance["grant_digest"] != digest
                        ):
                            raise TrustRegistryError("GRANT_ACCEPTANCE_SCOPE_MISMATCH")
                        if not callable(receiver_acceptance_key_loader) or not callable(
                            receiver_acceptance_public_key_loader
                        ):
                            raise TrustRegistryError(
                                "RECEIVER_ACCEPTANCE_TRUST_CONFIGURATION_INCOMPLETE"
                            )
                        acceptance_claims = {
                            key: value for key, value in acceptance.items()
                            if key != "receiver_receipt"
                        }
                        try:
                            verify_receiver_acceptance_receipt(
                                acceptance["receiver_receipt"], tenant_id=tenant,
                                receiver_institution_id=grant["institution_id"],
                                grant=issuer_grant, acceptance=acceptance_claims,
                                receiver_acceptance_key_loader=receiver_acceptance_key_loader,
                                receiver_acceptance_public_key_loader=(
                                    receiver_acceptance_public_key_loader
                                ),
                                clock=clock,
                            )
                        except Exception as exc:
                            raise TrustRegistryError(
                                "GRANT_ACCEPTANCE_RECEIPT_INVALID:" + str(exc)
                            ) from exc
                        cursor.execute(
                            """SELECT actor_ref,evidence_ref,payload_json
                               FROM authority_grant_events
                               WHERE tenant_id=%s AND grant_id=%s AND version=%s
                                 AND event_type='FEDERATED_GRANT_ACCEPTED'""",
                            (tenant, grant_id, grant["version"]),
                        )
                        events = cursor.fetchall()
                        if len(events) != 1:
                            raise TrustRegistryError(
                                "GRANT_ACCEPTANCE_AUDIT_EVENT_MISSING_OR_AMBIGUOUS"
                            )
                        event = events[0]
                        expected_payload = {
                            "grant_digest": digest,
                            "issuer_envelope": envelope,
                            "acceptance": acceptance,
                        }
                        if _object(event[2], "GRANT_EVENT_PAYLOAD_INVALID") != expected_payload:
                            raise TrustRegistryError("GRANT_ACCEPTANCE_AUDIT_PAYLOAD_MISMATCH")
                        if event[0] != approval_actor or event[1] != approval_ref:
                            raise TrustRegistryError("GRANT_ACCEPTANCE_AUDIT_CONTEXT_MISMATCH")
                        return grant
                    except TrustRegistryError:
                        raise
                    except Exception as exc:
                        raise TrustRegistryError(
                            "GRANT_PROVENANCE_VERIFICATION_FAILED"
                        ) from exc
