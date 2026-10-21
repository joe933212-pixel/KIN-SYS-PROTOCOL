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
                case_key TEXT PRIMARY KEY,
                correction_reviewer TEXT NOT NULL
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
            CREATE TABLE IF NOT EXISTS correction_log (
                correction_key TEXT PRIMARY KEY,
                replaces_operation TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                rationale TEXT NOT NULL,
                proposal_operation TEXT NOT NULL,
                FOREIGN KEY(proposal_operation) REFERENCES workflow_operations(operation_key),
                FOREIGN KEY(correction_key) REFERENCES workflow_operations(operation_key),
                FOREIGN KEY(replaces_operation) REFERENCES workflow_operations(operation_key)
            );
            CREATE TABLE IF NOT EXISTS correction_proposals (
                proposal_operation TEXT PRIMARY KEY, case_key TEXT NOT NULL, step_key TEXT NOT NULL,
                reviewer TEXT NOT NULL, proposed_result TEXT NOT NULL, rule_id TEXT NOT NULL,
                replaces_operation TEXT NOT NULL, rationale TEXT NOT NULL,
                FOREIGN KEY(proposal_operation) REFERENCES workflow_operations(operation_key)
            );
            CREATE TABLE IF NOT EXISTS correction_responses (
                proposal_operation TEXT PRIMARY KEY, response_operation TEXT NOT NULL UNIQUE,
                mandated_actor TEXT NOT NULL, response TEXT NOT NULL, basis TEXT NOT NULL,
                FOREIGN KEY(proposal_operation) REFERENCES correction_proposals(proposal_operation),
                FOREIGN KEY(response_operation) REFERENCES workflow_operations(operation_key)
            );
            CREATE TABLE IF NOT EXISTS correction_disputes (
                dispute_operation TEXT PRIMARY KEY, proposal_operation TEXT NOT NULL,
                refusal_operation TEXT NOT NULL, reviewer TEXT NOT NULL, basis TEXT NOT NULL,
                FOREIGN KEY(dispute_operation) REFERENCES workflow_operations(operation_key),
                FOREIGN KEY(proposal_operation) REFERENCES correction_proposals(proposal_operation),
                FOREIGN KEY(refusal_operation) REFERENCES workflow_operations(operation_key)
            );

        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def create_case(self, case_key: str, *, origin_authority: str,
                    destination_authority: str, origin_rule_version: str = "1.0",
                    destination_rule_version: str = "1.0", reviewer_authority: str = "REVIEWER") -> None:
        if not reviewer_authority:
            raise ValueError("INVALID_REVIEWER_AUTHORITY")
        self.db.execute("INSERT INTO workflow_cases(case_key, correction_reviewer) VALUES (?, ?)", (case_key, reviewer_authority))
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

    def correct_decision(self, *, event_id: str, consignment_id: str, stage: str,
                        actor_id: str, replacement_decision: str, rule_version: str,
                        supersedes_event_id: str, correction_basis: str) -> dict[str, Any]:
        """Submit a correction proposal without mutating institutional state."""
        if stage not in {"ORIGIN", "DESTINATION"}:
            raise ValueError("UNKNOWN_STAGE")
        if replacement_decision not in {"APPROVE", "REJECT"}:
            raise ValueError("UNKNOWN_DECISION")
        vals = (event_id, consignment_id, actor_id, rule_version, supersedes_event_id, correction_basis)
        if any(not isinstance(v, str) or not v for v in vals):
            raise ValueError("INVALID_FIELD")
        request = {"event_id": event_id, "consignment_id": consignment_id, "stage": stage,
                   "actor_id": actor_id, "decision": replacement_decision, "rule_version": rule_version,
                   "supersedes_event_id": supersedes_event_id, "correction_basis": correction_basis}
        digest = self._hash(request)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT * FROM workflow_operations WHERE operation_key=?", (event_id,)).fetchone()
            if old:
                if old["payload_hash"] != digest:
                    raise ValueError("OPERATION_ID_CONFLICT")
                self.db.commit()
                return {"status": old["result_status"], "reason": old["rejection_code"], "duplicate": True}
            case = self.db.execute("SELECT correction_reviewer FROM workflow_cases WHERE case_key=?", (consignment_id,)).fetchone()
            step = self.db.execute("SELECT * FROM institutional_steps WHERE case_key=? AND step_key=?", (consignment_id, stage)).fetchone()
            original = self.db.execute("SELECT * FROM workflow_audit WHERE operation_key=? AND case_key=? AND step_key=?", (supersedes_event_id, consignment_id, stage)).fetchone()
            if case is None or step is None:
                raise KeyError("CASE_OR_STAGE_NOT_FOUND")
            failure = None
            if actor_id != case["correction_reviewer"]:
                failure = "CORRECTION_AUTHORITY_MISMATCH"
            elif rule_version != step["active_rule"]:
                failure = "RULE_VERSION_MISMATCH"
            elif original is None or original["result_after"] not in {"APPROVE", "REJECT"}:
                failure = "SUPERSEDED_DECISION_NOT_FOUND"
            elif step["result"] != original["result_after"]:
                failure = "DECISION_NO_LONGER_CURRENT"
            elif replacement_decision == step["result"]:
                failure = "CORRECTION_HAS_NO_EFFECT"
            status = "REJECTED" if failure else "ACCEPTED"
            self.db.execute("INSERT INTO workflow_operations(operation_key, case_key, step_key, payload_hash, actor, requested_result, result_status, rejection_code) VALUES (?,?,?,?,?,?,?,?)",
                (event_id, consignment_id, stage, digest, actor_id, replacement_decision, status, failure))
            if not failure:
                self.db.execute("INSERT INTO correction_proposals(proposal_operation, case_key, step_key, reviewer, proposed_result, rule_id, replaces_operation, rationale) VALUES (?,?,?,?,?,?,?,?)",
                    (event_id, consignment_id, stage, actor_id, replacement_decision, rule_version, supersedes_event_id, correction_basis))
            self.db.commit()
            return {"status": status, "reason": failure, "duplicate": False}
        except Exception:
            self.db.rollback()
            raise

    def authorize_correction(self, *, event_id: str, consignment_id: str, stage: str,
                             actor_id: str, rule_version: str, proposal_event_id: str) -> dict[str, Any]:
        """Apply a proposal only when the institution's own active authority agrees."""
        vals = (event_id, consignment_id, stage, actor_id, rule_version, proposal_event_id)
        if any(not isinstance(v, str) or not v for v in vals):
            raise ValueError("INVALID_FIELD")
        if stage not in {"ORIGIN", "DESTINATION"}:
            raise ValueError("UNKNOWN_STAGE")
        digest = self._hash({"event_id": event_id, "consignment_id": consignment_id,
            "stage": stage, "actor_id": actor_id, "rule_version": rule_version,
            "proposal_event_id": proposal_event_id})
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT * FROM workflow_operations WHERE operation_key=?", (event_id,)).fetchone()
            if old:
                if old["payload_hash"] != digest:
                    raise ValueError("OPERATION_ID_CONFLICT")
                self.db.commit()
                return {"status": old["result_status"], "reason": old["rejection_code"], "duplicate": True}
            step = self.db.execute("SELECT * FROM institutional_steps WHERE case_key=? AND step_key=?", (consignment_id, stage)).fetchone()
            proposal = self.db.execute("SELECT * FROM correction_proposals WHERE proposal_operation=? AND case_key=? AND step_key=?", (proposal_event_id, consignment_id, stage)).fetchone()
            if step is None:
                raise KeyError("CASE_OR_STAGE_NOT_FOUND")
            failure = None
            if actor_id != step["mandated_actor"]:
                failure = "AUTHORITY_MISMATCH"
            elif not step["enabled"]:
                failure = "AUTHORITY_REVOKED"
            elif rule_version != step["active_rule"]:
                failure = "RULE_VERSION_MISMATCH"
            elif proposal is None:
                failure = "CORRECTION_PROPOSAL_NOT_FOUND"
            elif proposal["rule_id"] != rule_version:
                failure = "RULE_VERSION_MISMATCH"
            elif self.db.execute("SELECT 1 FROM correction_responses WHERE proposal_operation=?", (proposal_event_id,)).fetchone():
                failure = "PROPOSAL_ALREADY_RESPONDED"
            else:
                original = self.db.execute("SELECT result_after FROM workflow_audit WHERE operation_key=? AND case_key=? AND step_key=?", (proposal["replaces_operation"], consignment_id, stage)).fetchone()
                if original is None or step["result"] != original["result_after"]:
                    failure = "DECISION_NO_LONGER_CURRENT"
            status = "REJECTED" if failure else "ACCEPTED"
            self.db.execute("INSERT INTO workflow_operations(operation_key, case_key, step_key, payload_hash, actor, requested_result, result_status, rejection_code) VALUES (?,?,?,?,?,?,?,?)",
                (event_id, consignment_id, stage, digest, actor_id, proposal["proposed_result"] if proposal else "CORRECTION", status, failure))
            if failure:
                self.db.commit()
                return {"status": status, "reason": failure, "duplicate": False}
            self.db.execute("INSERT INTO correction_log(correction_key, replaces_operation, reviewer, rationale, proposal_operation) VALUES (?,?,?,?,?)",
                (event_id, proposal["replaces_operation"], actor_id, proposal["rationale"], proposal_event_id))
            changed = self.db.execute("UPDATE institutional_steps SET result=?, revision=revision+1 WHERE case_key=? AND step_key=? AND result=?",
                (proposal["proposed_result"], consignment_id, stage, step["result"])).rowcount
            if changed != 1:
                raise RuntimeError("CONCURRENT_STEP_CHANGE")
            self.db.execute("INSERT INTO workflow_audit(operation_key, case_key, step_key, decided_by, mandated_actor, rule_id, result_before, result_after) VALUES (?,?,?,?,?,?,?,?)",
                (event_id, consignment_id, stage, actor_id, step["mandated_actor"], rule_version, step["result"], proposal["proposed_result"]))
            self.db.commit()
            return {"status": status, "reason": None, "duplicate": False}
        except Exception:
            self.db.rollback()
            raise

    def refuse_correction(self, *, event_id: str, consignment_id: str, stage: str,
                          actor_id: str, rule_version: str, proposal_event_id: str,
                          refusal_basis: str) -> dict[str, Any]:
        """Record institutional refusal without mutating the stage result."""
        vals = (event_id, consignment_id, stage, actor_id, rule_version, proposal_event_id, refusal_basis)
        if any(not isinstance(v, str) or not v for v in vals): raise ValueError("INVALID_FIELD")
        if stage not in {"ORIGIN", "DESTINATION"}: raise ValueError("UNKNOWN_STAGE")
        digest = self._hash({"event_id": event_id, "consignment_id": consignment_id, "stage": stage,
            "actor_id": actor_id, "rule_version": rule_version, "proposal_event_id": proposal_event_id,
            "refusal_basis": refusal_basis})
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT * FROM workflow_operations WHERE operation_key=?", (event_id,)).fetchone()
            if old:
                if old["payload_hash"] != digest: raise ValueError("OPERATION_ID_CONFLICT")
                self.db.commit()
                return {"status": old["result_status"], "reason": old["rejection_code"], "duplicate": True}
            step = self.db.execute("SELECT * FROM institutional_steps WHERE case_key=? AND step_key=?", (consignment_id, stage)).fetchone()
            proposal = self.db.execute("SELECT * FROM correction_proposals WHERE proposal_operation=? AND case_key=? AND step_key=?", (proposal_event_id, consignment_id, stage)).fetchone()
            failure = None
            if step is None: raise KeyError("CASE_OR_STAGE_NOT_FOUND")
            if actor_id != step["mandated_actor"]: failure = "AUTHORITY_MISMATCH"
            elif not step["enabled"]: failure = "AUTHORITY_REVOKED"
            elif rule_version != step["active_rule"]: failure = "RULE_VERSION_MISMATCH"
            elif proposal is None: failure = "CORRECTION_PROPOSAL_NOT_FOUND"
            elif self.db.execute("SELECT 1 FROM correction_responses WHERE proposal_operation=?", (proposal_event_id,)).fetchone(): failure = "PROPOSAL_ALREADY_RESPONDED"
            else:
                original = self.db.execute("SELECT result_after FROM workflow_audit WHERE operation_key=? AND case_key=? AND step_key=?", (proposal["replaces_operation"], consignment_id, stage)).fetchone()
                if original is None or step["result"] != original["result_after"]: failure = "DECISION_NO_LONGER_CURRENT"
            status = "REJECTED" if failure else "ACCEPTED"
            self.db.execute("INSERT INTO workflow_operations(operation_key, case_key, step_key, payload_hash, actor, requested_result, result_status, rejection_code) VALUES (?,?,?,?,?,?,?,?)",
                (event_id, consignment_id, stage, digest, actor_id, "REFUSE_CORRECTION", status, failure))
            if not failure:
                self.db.execute("INSERT INTO correction_responses VALUES (?,?,?,?,?)", (proposal_event_id, event_id, actor_id, "REFUSED", refusal_basis))
            self.db.commit()
            return {"status": status, "reason": failure, "duplicate": False}
        except Exception:
            self.db.rollback()
            raise

    def dispute_correction_refusal(self, *, event_id: str, consignment_id: str, stage: str,
                                   actor_id: str, proposal_event_id: str, dispute_basis: str) -> dict[str, Any]:
        """Append a reviewer challenge without changing institutional state."""
        vals = (event_id, consignment_id, stage, actor_id, proposal_event_id, dispute_basis)
        if any(not isinstance(v, str) or not v for v in vals): raise ValueError("INVALID_FIELD")
        if stage not in {"ORIGIN", "DESTINATION"}: raise ValueError("UNKNOWN_STAGE")
        digest = self._hash({"event_id": event_id, "consignment_id": consignment_id, "stage": stage,
            "actor_id": actor_id, "proposal_event_id": proposal_event_id, "dispute_basis": dispute_basis})
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT * FROM workflow_operations WHERE operation_key=?", (event_id,)).fetchone()
            if old:
                if old["payload_hash"] != digest: raise ValueError("OPERATION_ID_CONFLICT")
                self.db.commit()
                return {"status": old["result_status"], "reason": old["rejection_code"], "duplicate": True}
            case = self.db.execute("SELECT correction_reviewer FROM workflow_cases WHERE case_key=?", (consignment_id,)).fetchone()
            proposal = self.db.execute("SELECT * FROM correction_proposals WHERE proposal_operation=? AND case_key=? AND step_key=?", (proposal_event_id, consignment_id, stage)).fetchone()
            response = self.db.execute("SELECT * FROM correction_responses WHERE proposal_operation=?", (proposal_event_id,)).fetchone()
            if case is None or proposal is None: raise KeyError("CASE_OR_PROPOSAL_NOT_FOUND")
            failure = None
            if actor_id != case["correction_reviewer"]: failure = "CORRECTION_AUTHORITY_MISMATCH"
            elif response is None or response["response"] != "REFUSED": failure = "REFUSAL_NOT_FOUND"
            status = "REJECTED" if failure else "ACCEPTED"
            self.db.execute("INSERT INTO workflow_operations(operation_key, case_key, step_key, payload_hash, actor, requested_result, result_status, rejection_code) VALUES (?,?,?,?,?,?,?,?)",
                (event_id, consignment_id, stage, digest, actor_id, "DISPUTE_CORRECTION_REFUSAL", status, failure))
            if not failure:
                self.db.execute("INSERT INTO correction_disputes VALUES (?,?,?,?,?)", (event_id, proposal_event_id, response["response_operation"], actor_id, dispute_basis))
            self.db.commit()
            return {"status": status, "reason": failure, "duplicate": False}
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
