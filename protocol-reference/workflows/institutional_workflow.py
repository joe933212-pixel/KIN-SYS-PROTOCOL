# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Experimental multi-institution workflow model for the KIN-SYS research candidate.

This is a separate research slice, not an extension of the current ProtocolStore
or a claim of CEE conformance. Each institution commits only its own stage.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


class WorkflowConflict(ValueError):
    """An operation identifier was reused with different content."""


class InstitutionalWorkflow:
    """SQLite model with separately governed origin and destination stages."""

    def __init__(self, database: str = ":memory:"):
        self.db = sqlite3.connect(database, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS consignments (
                consignment_id TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS stage_state (
                consignment_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                authority_id TEXT NOT NULL,
                authority_active INTEGER NOT NULL CHECK(authority_active IN (0,1)),
                rule_version TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT 'PENDING',
                version INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(consignment_id, stage),
                FOREIGN KEY(consignment_id) REFERENCES consignments(consignment_id)
            );
            CREATE TABLE IF NOT EXISTS stage_events (
                event_id TEXT PRIMARY KEY,
                consignment_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                digest TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                requested_decision TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                FOREIGN KEY(consignment_id, stage)
                    REFERENCES stage_state(consignment_id, stage)
            );
            CREATE TABLE IF NOT EXISTS stage_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                consignment_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                authority_id TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                prior_outcome TEXT NOT NULL,
                new_outcome TEXT NOT NULL,
                FOREIGN KEY(event_id) REFERENCES stage_events(event_id),
                FOREIGN KEY(consignment_id, stage)
                    REFERENCES stage_state(consignment_id, stage)
            );
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def create_case(self, consignment_id: str, *, origin_authority: str,
                    destination_authority: str, origin_rule_version: str = "1.0",
                    destination_rule_version: str = "1.0") -> None:
        self.db.execute("INSERT INTO consignments(consignment_id) VALUES (?)", (consignment_id,))
        self.db.executemany(
            """INSERT INTO stage_state
               (consignment_id, stage, authority_id, authority_active, rule_version)
               VALUES (?, ?, ?, 1, ?)""",
            [(consignment_id, "ORIGIN", origin_authority, origin_rule_version),
             (consignment_id, "DESTINATION", destination_authority, destination_rule_version)],
        )
        self.db.commit()

    def set_authority_active(self, consignment_id: str, stage: str, active: bool) -> None:
        self.db.execute(
            "UPDATE stage_state SET authority_active=? WHERE consignment_id=? AND stage=?",
            (int(active), consignment_id, stage),
        )
        self.db.commit()

    def set_rule_version(self, consignment_id: str, stage: str, version: str) -> None:
        self.db.execute(
            "UPDATE stage_state SET rule_version=? WHERE consignment_id=? AND stage=?",
            (version, consignment_id, stage),
        )
        self.db.commit()

    @staticmethod
    def _digest(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def decide(self, *, event_id: str, consignment_id: str, stage: str,
               actor_id: str, decision: str, rule_version: str) -> dict[str, Any]:
        if stage not in {"ORIGIN", "DESTINATION"}:
            raise ValueError("UNKNOWN_STAGE")
        if decision not in {"APPROVE", "REJECT"}:
            raise ValueError("UNKNOWN_DECISION")
        payload = {"event_id": event_id, "consignment_id": consignment_id,
                   "stage": stage, "actor_id": actor_id, "decision": decision,
                   "rule_version": rule_version}
        if any(not isinstance(v, str) or not v for v in payload.values()):
            raise ValueError("INVALID_FIELD")
        digest = self._digest(payload)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.db.execute("SELECT * FROM stage_events WHERE event_id=?", (event_id,)).fetchone()
            if prior:
                if prior["digest"] != digest:
                    raise WorkflowConflict("OPERATION_ID_CONFLICT")
                self.db.commit()
                return {"status": prior["status"], "reason": prior["reason"], "duplicate": True}
            row = self.db.execute(
                "SELECT * FROM stage_state WHERE consignment_id=? AND stage=?",
                (consignment_id, stage),
            ).fetchone()
            if row is None:
                raise KeyError("CASE_OR_STAGE_NOT_FOUND")
            reason = None
            if actor_id != row["authority_id"]:
                reason = "AUTHORITY_MISMATCH"
            elif not row["authority_active"]:
                reason = "AUTHORITY_REVOKED"
            elif rule_version != row["rule_version"]:
                reason = "RULE_VERSION_MISMATCH"
            elif row["outcome"] != "PENDING":
                reason = "STAGE_ALREADY_DECIDED"
            status = "REJECTED" if reason else "ACCEPTED"
            self.db.execute(
                """INSERT INTO stage_events
                   (event_id, consignment_id, stage, digest, actor_id,
                    requested_decision, status, reason) VALUES (?,?,?,?,?,?,?,?)""",
                (event_id, consignment_id, stage, digest, actor_id, decision, status, reason),
            )
            if reason:
                self.db.commit()
                return {"status": status, "reason": reason, "duplicate": False}
            self.db.execute(
                """UPDATE stage_state SET outcome=?, version=version+1
                   WHERE consignment_id=? AND stage=? AND outcome='PENDING'""",
                (decision, consignment_id, stage),
            )
            if self.db.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("STAGE_CHANGED_DURING_DECISION")
            self.db.execute(
                """INSERT INTO stage_history
                   (event_id, consignment_id, stage, actor_id, authority_id,
                    rule_version, prior_outcome, new_outcome)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (event_id, consignment_id, stage, actor_id, row["authority_id"],
                 row["rule_version"], row["outcome"], decision),
            )
            self.db.commit()
            return {"status": status, "reason": None, "duplicate": False}
        except Exception:
            self.db.rollback()
            raise

    def stage(self, consignment_id: str, stage: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM stage_state WHERE consignment_id=? AND stage=?",
            (consignment_id, stage),
        ).fetchone()
        if row is None:
            raise KeyError("CASE_OR_STAGE_NOT_FOUND")
        return dict(row)

    def history(self, consignment_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM stage_history WHERE consignment_id=? ORDER BY history_id",
            (consignment_id,),
        ).fetchall()
        return [dict(row) for row in rows]
