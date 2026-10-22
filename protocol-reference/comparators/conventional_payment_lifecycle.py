# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Independent conventional SQLite baseline for the payment lifecycle contract.

This module intentionally does not import or subclass payment_lifecycle.py.
It uses a distinct schema and independently written persistence path.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


class ConventionalPaymentLifecycle:
    def __init__(self, database: str = ":memory:"):
        self.connection = sqlite3.connect(database, timeout=5.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS liabilities (
                liability_key TEXT PRIMARY KEY,
                value_minor INTEGER NOT NULL CHECK(value_minor >= 0),
                unit TEXT NOT NULL,
                provenance TEXT NOT NULL,
                state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transfers (
                transfer_key TEXT PRIMARY KEY,
                liability_key TEXT NOT NULL UNIQUE REFERENCES liabilities(liability_key),
                fingerprint TEXT NOT NULL,
                value_minor INTEGER NOT NULL,
                unit TEXT NOT NULL,
                state TEXT NOT NULL,
                provider_key TEXT,
                settlement_key TEXT,
                reconciliation_result TEXT
            );
            CREATE TABLE IF NOT EXISTS transfer_log (
                log_key TEXT PRIMARY KEY,
                transfer_key TEXT NOT NULL REFERENCES transfers(transfer_key),
                category TEXT NOT NULL,
                record TEXT NOT NULL
            );
        """)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create_obligation(self, obligation_id: str, amount_minor: int, currency: str,
                          source_ref: str) -> dict[str, Any]:
        if not obligation_id or not currency or not source_ref:
            raise ValueError("OBLIGATION_FIELDS_REQUIRED")
        if type(amount_minor) is not int or amount_minor < 0:
            raise ValueError("INVALID_OBLIGATION_AMOUNT")
        row = self.connection.execute(
            "SELECT * FROM liabilities WHERE liability_key=?", (obligation_id,)
        ).fetchone()
        if row:
            if (row["value_minor"], row["unit"], row["provenance"]) != (amount_minor, currency, source_ref):
                raise ValueError("OBLIGATION_ID_CONFLICT")
            return dict(row)
        self.connection.execute(
            "INSERT INTO liabilities VALUES (?, ?, ?, ?, 'RECORDED')",
            (obligation_id, amount_minor, currency, source_ref),
        )
        self.connection.commit()
        return dict(self.connection.execute(
            "SELECT * FROM liabilities WHERE liability_key=?", (obligation_id,)
        ).fetchone())

    def create_instruction(self, payment_id: str, obligation_id: str) -> dict[str, Any]:
        if not payment_id:
            raise ValueError("PAYMENT_ID_REQUIRED")
        liability = self.connection.execute(
            "SELECT * FROM liabilities WHERE liability_key=?", (obligation_id,)
        ).fetchone()
        if liability is None:
            raise ValueError("OBLIGATION_NOT_FOUND")
        request = {"payment_id": payment_id, "obligation_id": obligation_id,
                   "amount_minor": liability["value_minor"], "currency": liability["unit"]}
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        prior = self.connection.execute(
            "SELECT * FROM transfers WHERE transfer_key=?", (payment_id,)
        ).fetchone()
        if prior:
            if prior["fingerprint"] != digest:
                raise ValueError("PAYMENT_ID_CONFLICT")
            return self._normalise_transfer(prior)
        try:
            self.connection.execute(
                """INSERT INTO transfers
                   (transfer_key, liability_key, fingerprint, value_minor, unit, state)
                   VALUES (?, ?, ?, ?, ?, 'INSTRUCTION_CREATED')""",
                (payment_id, obligation_id, digest, liability["value_minor"], liability["unit"]),
            )
            self.connection.execute(
                "UPDATE liabilities SET state='PAYMENT_INSTRUCTED' WHERE liability_key=?",
                (obligation_id,),
            )
            self._append(payment_id + ":INSTRUCTION", payment_id, "INSTRUCTION_CREATED", request)
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ValueError("OBLIGATION_ALREADY_HAS_PAYMENT_INSTRUCTION") from exc
        return self.get_payment(payment_id)

    def record_provider_ack(self, payment_id: str, event_id: str, acknowledgement: str,
                            provider_reference: str | None = None) -> dict[str, Any]:
        if acknowledgement not in {"ACCEPTED", "DECLINED", "UNKNOWN"}:
            raise ValueError("INVALID_PROVIDER_ACKNOWLEDGEMENT")
        transfer = self._transfer(payment_id)
        if transfer["state"] == "SETTLED":
            raise ValueError("PAYMENT_ALREADY_SETTLED")
        record = {"acknowledgement": acknowledgement, "provider_reference": provider_reference}
        self._append(event_id, payment_id, "PROVIDER_ACKNOWLEDGEMENT", record)
        state = {
            "ACCEPTED": "PROVIDER_ACCEPTED_SETTLEMENT_UNCONFIRMED",
            "DECLINED": "PROVIDER_DECLINED",
            "UNKNOWN": "UNKNOWN_PENDING_RECONCILIATION",
        }[acknowledgement]
        self.connection.execute(
            "UPDATE transfers SET state=?, provider_key=? WHERE transfer_key=?",
            (state, provider_reference, payment_id),
        )
        self.connection.commit()
        return self.get_payment(payment_id)

    def record_settlement(self, payment_id: str, event_id: str, settlement_reference: str,
                          *, amount_minor: int, currency: str) -> dict[str, Any]:
        transfer = self._transfer(payment_id)
        if not settlement_reference:
            raise ValueError("SETTLEMENT_REFERENCE_REQUIRED")
        if amount_minor != transfer["value_minor"] or currency != transfer["unit"]:
            raise ValueError("SETTLEMENT_AMOUNT_OR_CURRENCY_MISMATCH")
        request = {
            "payment_id": transfer["transfer_key"],
            "obligation_id": transfer["liability_key"],
            "amount_minor": transfer["value_minor"],
            "currency": transfer["unit"],
        }
        expected_fingerprint = hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if transfer["fingerprint"] != expected_fingerprint:
            raise ValueError("PAYMENT_RECORD_INTEGRITY_MISMATCH")
        liability = self.connection.execute(
            "SELECT value_minor, unit FROM liabilities WHERE liability_key=?",
            (transfer["liability_key"],),
        ).fetchone()
        if liability is None or (
            liability["value_minor"] != transfer["value_minor"]
            or liability["unit"] != transfer["unit"]
        ):
            raise ValueError("OBLIGATION_PAYMENT_MISMATCH")
        record = {"settlement_reference": settlement_reference,
                  "amount_minor": amount_minor, "currency": currency}
        self._append(event_id, payment_id, "SETTLEMENT_EVIDENCE", record)
        if transfer["state"] == "SETTLED":
            if transfer["settlement_key"] != settlement_reference:
                self.connection.rollback()
                raise ValueError("CONFLICTING_SETTLEMENT_EVIDENCE")
            self.connection.commit()
            return self.get_payment(payment_id)
        self.connection.execute(
            "UPDATE transfers SET state='SETTLED', settlement_key=? WHERE transfer_key=?",
            (settlement_reference, payment_id),
        )
        self.connection.execute(
            "UPDATE liabilities SET state='SETTLED' WHERE liability_key=?",
            (transfer["liability_key"],),
        )
        self.connection.commit()
        return self.get_payment(payment_id)

    def record_reconciliation(self, payment_id: str, event_id: str, finding: str,
                              provider_reference: str | None = None) -> dict[str, Any]:
        if finding not in {"SETTLED", "NOT_SETTLED", "UNKNOWN"}:
            raise ValueError("INVALID_RECONCILIATION_FINDING")
        transfer = self._transfer(payment_id)
        self._append(event_id, payment_id, "RECONCILIATION",
                     {"finding": finding, "provider_reference": provider_reference})
        if finding == "SETTLED" and transfer["state"] != "SETTLED":
            self.connection.rollback()
            raise ValueError("SETTLEMENT_EVIDENCE_REQUIRED")
        if transfer["state"] == "SETTLED" and finding != "SETTLED":
            self.connection.rollback()
            raise ValueError("RECONCILIATION_CONFLICTS_WITH_SETTLEMENT")
        if transfer["state"] != "SETTLED":
            state = "RECONCILED_NOT_SETTLED" if finding == "NOT_SETTLED" else "UNKNOWN_PENDING_RECONCILIATION"
            self.connection.execute(
                "UPDATE transfers SET state=?, reconciliation_result=? WHERE transfer_key=?",
                (state, finding, payment_id),
            )
        else:
            self.connection.execute(
                "UPDATE transfers SET reconciliation_result=? WHERE transfer_key=?",
                (finding, payment_id),
            )
        self.connection.commit()
        return self.get_payment(payment_id)

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        return self._normalise_transfer(self._transfer(payment_id))

    def history(self, payment_id: str) -> list[dict[str, Any]]:
        return [
            {"event_id": row["log_key"], "payment_id": row["transfer_key"],
             "event_type": row["category"], "evidence": json.loads(row["record"])}
            for row in self.connection.execute(
                "SELECT * FROM transfer_log WHERE transfer_key=? ORDER BY rowid", (payment_id,)
            )
        ]

    def _transfer(self, payment_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM transfers WHERE transfer_key=?", (payment_id,)
        ).fetchone()
        if row is None:
            raise ValueError("PAYMENT_NOT_FOUND")
        return row

    @staticmethod
    def _normalise_transfer(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "payment_id": row["transfer_key"], "obligation_id": row["liability_key"],
            "amount_minor": row["value_minor"], "currency": row["unit"],
            "status": row["state"], "provider_reference": row["provider_key"],
            "settlement_reference": row["settlement_key"],
            "last_reconciliation": row["reconciliation_result"],
        }

    def _append(self, event_id: str, payment_id: str, event_type: str,
                evidence: dict[str, Any]) -> None:
        if not event_id:
            raise ValueError("EVENT_ID_REQUIRED")
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        row = self.connection.execute(
            "SELECT transfer_key, category, record FROM transfer_log WHERE log_key=?", (event_id,)
        ).fetchone()
        if row:
            if (row["transfer_key"], row["category"], row["record"]) != (payment_id, event_type, payload):
                raise ValueError("EVENT_ID_CONFLICT")
            return
        self.connection.execute(
            "INSERT INTO transfer_log VALUES (?, ?, ?, ?)",
            (event_id, payment_id, event_type, payload),
        )
