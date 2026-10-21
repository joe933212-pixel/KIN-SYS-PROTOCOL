# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Independent conventional workflow/API baseline for the institutional-stage contract.

Uses a distinct schema and implementation; it does not import KIN-SYS workflow code.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


class ConventionalWorkflow:
    def __init__(self, database: str = ":memory:"):
        self.db = sqlite3.connect(database, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS workflow_cases (
                case_key TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS institutional_steps (
                case_key TEXT NOT NULL,
                step_key TEXT NOT NULL,
                mandated_actor TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                active_rule TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT 'PENDING',
                revision INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(case_key, step_key),
                FOREIGN KEY(case_key) REFERENCES workflow_cases(case_key)
            );
            CREATE TABLE IF NOT EXISTS workflow_operations (
                operation_key TEXT PRIMARY KEY,
                case_key TEXT NOT NULL,
                step_key TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                actor TEXT NOT NULL,
                requested_result TEXT NOT NULL,
                result_status TEXT NOT NULL,
                rejection_code TEXT,
                FOREIGN KEY(case_key, step_key)
                    REFERENCES institutional_steps(case_key, step_key)
            );
            CREATE TABLE IF NOT EXISTS workflow_audit (
                audit_key INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_key TEXT NOT NULL UNIQUE,
                case_key TEXT NOT NULL,
                step_key TEXT NOT NULL,
                decided_by TEXT NOT NULL,
                mandated_actor TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                result_before TEXT NOT NULL,
                result_after TEXT NOT NULL,
                FOREIGN KEY(operation_key) REFERENCES workflow_operations(operation_key),
                FOREIGN KEY(case_key, step_key)
                    REFERENCES institutional_steps(case_key, step_key)
            );
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def create_case(self, case_key: str, *, origin_authority: str,
                    destination_authority: str, origin_rule_version: str = "1.0",
                    destination_rule_version: str = "1.0") -> None:
        self.db.execute("INSERT INTO workflow_cases(case_key) VALUES (?)", (case_key,))
        self.db.executemany(
            """INSERT INTO institutional_steps
               (case_key, step_key, mandated_actor, enabled, active_rule)
               VALUES (?, ?, ?, 1, ?)""",
            [(case_key, "ORIGIN", origin_authority, origin_rule_version),
             (case_key, "DESTINATION", destination_authority, destination_rule_version)],
        )
        self.db.commit()

    def set_authority_active(self, case_key: str, stage: str, active: bool) -> None:
        self.db.execute(
            "UPDATE institutional_steps SET enabled=? WHERE case_key=? AND step_key=?",
            (int(active), case_key, stage),
        )
        self.db.commit()

    def set_rule_version(self, case_key: str, stage: str, version: str) -> None:
        self.db.execute(
            "UPDATE institutional_steps SET active_rule=? WHERE case_key=? AND step_key=?",
            (version, case_key, stage),
        )
        self.db.commit()

    @staticmethod
    def _hash(payload: dict[str, Any]) -> str:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def decide(self, *, event_id: str, consignment_id: str, stage: str,
               actor_id: str, decision: str, rule_version: str) -> dict[str, Any]:
        if stage not in {"ORIGIN", "DESTINATION"}:
            raise ValueError("UNKNOWN_STAGE")
        if decision not in {"APPROVE", "REJECT"}:
            raise ValueError("UNKNOWN_DECISION")
        request = {"event_id": event_id, "consignment_id": consignment_id,
                   "stage": stage, "actor_id": actor_id, "decision": decision,
                   "rule_version": rule_version}
        if any(not isinstance(v, str) or not v for v in request.values()):
            raise ValueError("INVALID_FIELD")
        payload_hash = self._hash(request)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute(
                "SELECT * FROM workflow_operations WHERE operation_key=?", (event_id,)
            ).fetchone()
            if old:
                if old["payload_hash"] != payload_hash:
                    raise ValueError("OPERATION_ID_CONFLICT")
                self.db.commit()
                return {"status": old["result_status"], "reason": old["rejection_code"], "duplicate": True}
            step = self.db.execute(
                "SELECT * FROM institutional_steps WHERE case_key=? AND step_key=?",
                (consignment_id, stage),
            ).fetchone()
            if step is None:
                raise KeyError("CASE_OR_STAGE_NOT_FOUND")
            failure = None
            if actor_id != step["mandated_actor"]:
                failure = "AUTHORITY_MISMATCH"
            elif not step["enabled"]:
                failure = "AUTHORITY_REVOKED"
            elif rule_version != step["active_rule"]:
                failure = "RULE_VERSION_MISMATCH"
            elif step["result"] != "PENDING":
                failure = "STAGE_ALREADY_DECIDED"
            result_status = "REJECTED" if failure else "ACCEPTED"
            self.db.execute(
                """INSERT INTO workflow_operations
                   (operation_key, case_key, step_key, payload_hash, actor,
                    requested_result, result_status, rejection_code)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (event_id, consignment_id, stage, payload_hash, actor_id, decision, result_status, failure),
            )
            if failure:
                self.db.commit()
                return {"status": result_status, "reason": failure, "duplicate": False}
            changed = self.db.execute(
                """UPDATE institutional_steps SET result=?, revision=revision+1
                   WHERE case_key=? AND step_key=? AND result='PENDING' AND revision=?""",
                (decision, consignment_id, stage, step["revision"]),
            ).rowcount
            if changed != 1:
                raise RuntimeError("CONCURRENT_STEP_CHANGE")
            self.db.execute(
                """INSERT INTO workflow_audit
                   (operation_key, case_key, step_key, decided_by, mandated_actor,
                    rule_id, result_before, result_after)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (event_id, consignment_id, stage, actor_id, step["mandated_actor"],
                 step["active_rule"], step["result"], decision),
            )
            self.db.commit()
            return {"status": result_status, "reason": None, "duplicate": False}
        except Exception:
            self.db.rollback()
            raise

    def stage(self, consignment_id: str, stage: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM institutional_steps WHERE case_key=? AND step_key=?",
            (consignment_id, stage),
        ).fetchone()
        if row is None:
            raise KeyError("CASE_OR_STAGE_NOT_FOUND")
        return dict(row)

    def history(self, consignment_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM workflow_audit WHERE case_key=? ORDER BY audit_key",
            (consignment_id,),
        ).fetchall()
        return [dict(row) for row in rows]
