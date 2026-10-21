# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Local reference implementation for the KIN-SYS transition contract.

Research prototype only. This module is not a network protocol or a
cryptographic authentication implementation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any


class ProtocolError(Exception):
    """Base class for explicit protocol-slice errors."""


class EventIdConflict(ProtocolError):
    """An event identifier was reused for different request content."""


class ProcessNotFound(ProtocolError):
    """The referenced process does not exist."""


@dataclass(frozen=True)
class Decision:
    status: str
    event_id: str
    process_id: str
    from_state: str | None = None
    to_state: str | None = None
    history_id: str | None = None
    reason: str | None = None
    duplicate: bool = False


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _request_digest(event: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(event).encode("utf-8")).hexdigest()


class ProtocolStore:
    """Single-process SQLite implementation of the minimal transition contract."""

    def __init__(self, database: str = ":memory:"):
        self.db = sqlite3.connect(database)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS processes (
                process_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                state_version INTEGER NOT NULL DEFAULT 0,
                authority_id TEXT NOT NULL,
                authority_active INTEGER NOT NULL CHECK (authority_active IN (0, 1)),
                rule_set TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                eligible INTEGER NOT NULL CHECK (eligible IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                process_id TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reason TEXT,
                FOREIGN KEY(process_id) REFERENCES processes(process_id)
            );
            CREATE TABLE IF NOT EXISTS transition_history (
                history_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                process_id TEXT NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                authority_id TEXT NOT NULL,
                rule_set TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                FOREIGN KEY(event_id) REFERENCES events(event_id),
                FOREIGN KEY(process_id) REFERENCES processes(process_id)
            );
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def create_process(
        self,
        *,
        process_id: str,
        initial_state: str = "PENDING",
        authority_id: str = "AUTHORITY-001",
        authority_active: bool = True,
        rule_set: str = "LICENCE-RULES",
        rule_version: str = "1.0",
        eligible: bool = True,
    ) -> None:
        self.db.execute(
            """INSERT INTO processes
               (process_id, state, authority_id, authority_active,
                rule_set, rule_version, eligible)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                process_id, initial_state, authority_id, int(authority_active),
                rule_set, rule_version, int(eligible),
            ),
        )
        self.db.commit()

    def set_authority_active(self, process_id: str, active: bool) -> None:
        self.db.execute(
            "UPDATE processes SET authority_active = ? WHERE process_id = ?",
            (int(active), process_id),
        )
        self.db.commit()

    def set_rule(self, process_id: str, *, version: str, eligible: bool) -> None:
        self.db.execute(
            "UPDATE processes SET rule_version = ?, eligible = ? WHERE process_id = ?",
            (version, int(eligible), process_id),
        )
        self.db.commit()

    def process(self, process_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM processes WHERE process_id = ?", (process_id,)
        ).fetchone()
        if row is None:
            raise ProcessNotFound(process_id)
        return dict(row)

    def history(self, process_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM transition_history WHERE process_id = ? ORDER BY rowid",
            (process_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def event(self, event_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None

    def execute(self, event: dict[str, Any], *, fail_before_commit: bool = False) -> Decision:
        required = {
            "event_id", "process_id", "actor_id", "event_type",
            "rule_set", "rule_version",
        }
        if not isinstance(event, dict) or required - set(event):
            missing = sorted(required - set(event)) if isinstance(event, dict) else sorted(required)
            raise ValueError(f"INVALID_EVENT_FIELDS:{','.join(missing)}")
        if any(not isinstance(event[key], str) or not event[key] for key in required):
            raise ValueError("INVALID_EVENT_FIELD_TYPE_OR_VALUE")

        event = dict(event)
        digest = _request_digest(event)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute(
                "SELECT * FROM events WHERE event_id = ?", (event["event_id"],)
            ).fetchone()
            if previous:
                if previous["request_digest"] != digest:
                    raise EventIdConflict("EVENT_ID_REUSED_WITH_DIFFERENT_CONTENT")
                history = self.db.execute(
                    "SELECT * FROM transition_history WHERE event_id = ?",
                    (event["event_id"],),
                ).fetchone()
                self.db.commit()
                return Decision(
                    status=previous["outcome"],
                    event_id=event["event_id"],
                    process_id=event["process_id"],
                    from_state=history["from_state"] if history else None,
                    to_state=history["to_state"] if history else None,
                    history_id=history["history_id"] if history else None,
                    reason=previous["reason"],
                    duplicate=True,
                )

            process = self.db.execute(
                "SELECT * FROM processes WHERE process_id = ?", (event["process_id"],)
            ).fetchone()
            if process is None:
                raise ProcessNotFound(event["process_id"])

            reason = None
            if event["actor_id"] != process["authority_id"]:
                reason = "AUTHORITY_MISMATCH"
            elif not process["authority_active"]:
                reason = "AUTHORITY_REVOKED"
            elif event["rule_set"] != process["rule_set"] or event["rule_version"] != process["rule_version"]:
                reason = "RULE_VERSION_MISMATCH"
            elif not process["eligible"]:
                reason = "RULE_REJECTED"
            elif event["event_type"] != "ACTIVATE" or process["state"] != "PENDING":
                reason = "UNDEFINED_TRANSITION"

            if reason:
                self.db.execute(
                    """INSERT INTO events
                       (event_id, process_id, request_digest, actor_id,
                        event_type, outcome, reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event["event_id"], event["process_id"], digest,
                        event["actor_id"], event["event_type"], "REJECTED", reason,
                    ),
                )
                self.db.commit()
                return Decision(
                    status="REJECTED", event_id=event["event_id"],
                    process_id=event["process_id"], reason=reason,
                )

            from_state = process["state"]
            to_state = "ACTIVE"
            history_id = "H-" + hashlib.sha256(event["event_id"].encode("utf-8")).hexdigest()[:16]
            self.db.execute(
                """INSERT INTO events
                   (event_id, process_id, request_digest, actor_id,
                    event_type, outcome, reason)
                   VALUES (?, ?, ?, ?, ?, ?, NULL)""",
                (
                    event["event_id"], event["process_id"], digest,
                    event["actor_id"], event["event_type"], "ACCEPTED",
                ),
            )
            self.db.execute(
                """UPDATE processes SET state = ?, state_version = state_version + 1
                   WHERE process_id = ? AND state = ?""",
                (to_state, event["process_id"], from_state),
            )
            if self.db.execute("SELECT changes()").fetchone()[0] != 1:
                raise ProtocolError("STATE_CHANGED_DURING_TRANSITION")
            if fail_before_commit:
                raise RuntimeError("INJECTED_FAILURE_BEFORE_COMMIT")
            self.db.execute(
                """INSERT INTO transition_history
                   (history_id, event_id, process_id, from_state, to_state,
                    authority_id, rule_set, rule_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    history_id, event["event_id"], event["process_id"],
                    from_state, to_state, process["authority_id"],
                    process["rule_set"], process["rule_version"],
                ),
            )
            self.db.commit()
            return Decision(
                status="ACCEPTED", event_id=event["event_id"],
                process_id=event["process_id"], from_state=from_state,
                to_state=to_state, history_id=history_id,
            )
        except Exception:
            self.db.rollback()
            raise
