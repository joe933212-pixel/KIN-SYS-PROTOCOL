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
                consignment_id TEXT PRIMARY KEY,
                reviewer_authority_id TEXT NOT NULL
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
            CREATE TABLE IF NOT EXISTS stage_corrections (
                correction_event_id TEXT PRIMARY KEY,
                superseded_event_id TEXT NOT NULL,
                correction_authority_id TEXT NOT NULL,
                correction_basis TEXT NOT NULL,
                proposal_event_id TEXT NOT NULL,
                FOREIGN KEY(proposal_event_id) REFERENCES stage_events(event_id),
                FOREIGN KEY(correction_event_id) REFERENCES stage_events(event_id),
                FOREIGN KEY(superseded_event_id) REFERENCES stage_events(event_id)
            );
            CREATE TABLE IF NOT EXISTS correction_proposals (
                proposal_event_id TEXT PRIMARY KEY, consignment_id TEXT NOT NULL, stage TEXT NOT NULL,
                reviewer_id TEXT NOT NULL, proposed_decision TEXT NOT NULL, rule_version TEXT NOT NULL,
                superseded_event_id TEXT NOT NULL, correction_basis TEXT NOT NULL,
                FOREIGN KEY(proposal_event_id) REFERENCES stage_events(event_id)
            );
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def create_case(self, consignment_id: str, *, origin_authority: str,
                    destination_authority: str, origin_rule_version: str = "1.0",
                    destination_rule_version: str = "1.0", reviewer_authority: str = "REVIEWER") -> None:
        if not reviewer_authority:
            raise ValueError("INVALID_REVIEWER_AUTHORITY")
        self.db.execute("INSERT INTO consignments(consignment_id, reviewer_authority_id) VALUES (?, ?)", (consignment_id, reviewer_authority))
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

    def correct_decision(self, *, event_id: str, consignment_id: str, stage: str,
                        actor_id: str, replacement_decision: str, rule_version: str,
                        supersedes_event_id: str, correction_basis: str) -> dict[str, Any]:
        """Submit a correction proposal; this method never changes the stage outcome."""
        if stage not in {"ORIGIN", "DESTINATION"}:
            raise ValueError("UNKNOWN_STAGE")
        if replacement_decision not in {"APPROVE", "REJECT"}:
            raise ValueError("UNKNOWN_DECISION")
        values = (event_id, consignment_id, actor_id, rule_version, supersedes_event_id, correction_basis)
        if any(not isinstance(v, str) or not v for v in values):
            raise ValueError("INVALID_FIELD")
        payload = {"event_id": event_id, "consignment_id": consignment_id, "stage": stage,
                   "actor_id": actor_id, "decision": replacement_decision, "rule_version": rule_version,
                   "supersedes_event_id": supersedes_event_id, "correction_basis": correction_basis}
        digest = self._digest(payload)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT * FROM stage_events WHERE event_id=?", (event_id,)).fetchone()
            if old:
                if old["digest"] != digest:
                    raise WorkflowConflict("OPERATION_ID_CONFLICT")
                self.db.commit()
                return {"status": old["status"], "reason": old["reason"], "duplicate": True}
            case = self.db.execute("SELECT reviewer_authority_id FROM consignments WHERE consignment_id=?", (consignment_id,)).fetchone()
            row = self.db.execute("SELECT * FROM stage_state WHERE consignment_id=? AND stage=?", (consignment_id, stage)).fetchone()
            original = self.db.execute("SELECT * FROM stage_history WHERE event_id=? AND consignment_id=? AND stage=?", (supersedes_event_id, consignment_id, stage)).fetchone()
            if case is None or row is None:
                raise KeyError("CASE_OR_STAGE_NOT_FOUND")
            reason = None
            if actor_id != case["reviewer_authority_id"]:
                reason = "CORRECTION_AUTHORITY_MISMATCH"
            elif rule_version != row["rule_version"]:
                reason = "RULE_VERSION_MISMATCH"
            elif original is None or original["new_outcome"] not in {"APPROVE", "REJECT"}:
                reason = "SUPERSEDED_DECISION_NOT_FOUND"
            elif row["outcome"] != original["new_outcome"]:
                reason = "DECISION_NO_LONGER_CURRENT"
            elif replacement_decision == row["outcome"]:
                reason = "CORRECTION_HAS_NO_EFFECT"
            status = "REJECTED" if reason else "ACCEPTED"
            self.db.execute("INSERT INTO stage_events(event_id, consignment_id, stage, digest, actor_id, requested_decision, status, reason) VALUES (?,?,?,?,?,?,?,?)",
                            (event_id, consignment_id, stage, digest, actor_id, replacement_decision, status, reason))
            if not reason:
                self.db.execute("INSERT INTO correction_proposals(proposal_event_id, consignment_id, stage, reviewer_id, proposed_decision, rule_version, superseded_event_id, correction_basis) VALUES (?,?,?,?,?,?,?,?)",
                                (event_id, consignment_id, stage, actor_id, replacement_decision, rule_version, supersedes_event_id, correction_basis))
            self.db.commit()
            return {"status": status, "reason": reason, "duplicate": False}
        except Exception:
            self.db.rollback()
            raise

    def authorize_correction(self, *, event_id: str, consignment_id: str, stage: str,
                             actor_id: str, rule_version: str, proposal_event_id: str) -> dict[str, Any]:
        """Apply a proposal only with the active authority of the affected institution."""
        vals = (event_id, consignment_id, stage, actor_id, rule_version, proposal_event_id)
        if any(not isinstance(v, str) or not v for v in vals):
            raise ValueError("INVALID_FIELD")
        if stage not in {"ORIGIN", "DESTINATION"}:
            raise ValueError("UNKNOWN_STAGE")
        digest = self._digest({"event_id": event_id, "consignment_id": consignment_id,
            "stage": stage, "actor_id": actor_id, "rule_version": rule_version,
            "proposal_event_id": proposal_event_id})
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT * FROM stage_events WHERE event_id=?", (event_id,)).fetchone()
            if old:
                if old["digest"] != digest:
                    raise WorkflowConflict("OPERATION_ID_CONFLICT")
                self.db.commit()
                return {"status": old["status"], "reason": old["reason"], "duplicate": True}
            row = self.db.execute("SELECT * FROM stage_state WHERE consignment_id=? AND stage=?", (consignment_id, stage)).fetchone()
            proposal = self.db.execute("SELECT * FROM correction_proposals WHERE proposal_event_id=? AND consignment_id=? AND stage=?", (proposal_event_id, consignment_id, stage)).fetchone()
            if row is None:
                raise KeyError("CASE_OR_STAGE_NOT_FOUND")
            reason = None
            original = None
            if actor_id != row["authority_id"]:
                reason = "AUTHORITY_MISMATCH"
            elif not row["authority_active"]:
                reason = "AUTHORITY_REVOKED"
            elif rule_version != row["rule_version"]:
                reason = "RULE_VERSION_MISMATCH"
            elif proposal is None:
                reason = "CORRECTION_PROPOSAL_NOT_FOUND"
            elif proposal["rule_version"] != rule_version:
                reason = "RULE_VERSION_MISMATCH"
            else:
                original = self.db.execute("SELECT new_outcome FROM stage_history WHERE event_id=? AND consignment_id=? AND stage=?", (proposal["superseded_event_id"], consignment_id, stage)).fetchone()
                if original is None or row["outcome"] != original["new_outcome"]:
                    reason = "DECISION_NO_LONGER_CURRENT"
            status = "REJECTED" if reason else "ACCEPTED"
            self.db.execute("INSERT INTO stage_events(event_id, consignment_id, stage, digest, actor_id, requested_decision, status, reason) VALUES (?,?,?,?,?,?,?,?)",
                (event_id, consignment_id, stage, digest, actor_id, proposal["proposed_decision"] if proposal else "CORRECTION", status, reason))
            if reason:
                self.db.commit()
                return {"status": status, "reason": reason, "duplicate": False}
            self.db.execute("INSERT INTO stage_corrections(correction_event_id, superseded_event_id, correction_authority_id, correction_basis, proposal_event_id) VALUES (?,?,?,?,?)",
                (event_id, proposal["superseded_event_id"], actor_id, proposal["correction_basis"], proposal_event_id))
            changed = self.db.execute("UPDATE stage_state SET outcome=?, version=version+1 WHERE consignment_id=? AND stage=? AND outcome=?",
                (proposal["proposed_decision"], consignment_id, stage, row["outcome"])).rowcount
            if changed != 1:
                raise RuntimeError("STAGE_CHANGED_DURING_CORRECTION")
            self.db.execute("INSERT INTO stage_history(event_id, consignment_id, stage, actor_id, authority_id, rule_version, prior_outcome, new_outcome) VALUES (?,?,?,?,?,?,?,?)",
                (event_id, consignment_id, stage, actor_id, row["authority_id"], rule_version, row["outcome"], proposal["proposed_decision"]))
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
