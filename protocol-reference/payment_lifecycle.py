# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Synthetic payment lifecycle boundary with durable idempotency semantics.

This module models records and evidence only. It does not connect to a bank,
mobile-money provider, tax authority, or ledger that moves real funds.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any


class PaymentLifecycle:
    """SQLite-backed payment records; unknown outcomes require reconciliation."""

    def __init__(self, database: str = ":memory:"):
        self.db = sqlite3.connect(database, timeout=10.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 10000")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS obligations (
                obligation_id TEXT PRIMARY KEY,
                amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
                currency TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payments (
                payment_id TEXT PRIMARY KEY,
                obligation_id TEXT NOT NULL UNIQUE REFERENCES obligations(obligation_id),
                request_hash TEXT NOT NULL,
                amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                provider_reference TEXT,
                settlement_reference TEXT,
                last_reconciliation TEXT
            );
            CREATE TABLE IF NOT EXISTS payment_events (
                event_id TEXT PRIMARY KEY,
                payment_id TEXT NOT NULL REFERENCES payments(payment_id),
                event_type TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def create_obligation(
        self, obligation_id: str, amount_minor: int, currency: str, source_ref: str
    ) -> dict[str, Any]:
        if not obligation_id or not currency or not source_ref:
            raise ValueError("OBLIGATION_FIELDS_REQUIRED")
        if type(amount_minor) is not int or amount_minor < 0:
            raise ValueError("INVALID_OBLIGATION_AMOUNT")
        existing = self.db.execute(
            "SELECT * FROM obligations WHERE obligation_id=?", (obligation_id,)
        ).fetchone()
        if existing:
            if (existing["amount_minor"], existing["currency"], existing["source_ref"]) != (
                amount_minor, currency, source_ref
            ):
                raise ValueError("OBLIGATION_ID_CONFLICT")
            return dict(existing)
        self.db.execute(
            "INSERT INTO obligations VALUES (?, ?, ?, ?, 'RECORDED')",
            (obligation_id, amount_minor, currency, source_ref),
        )
        self.db.commit()
        return dict(self.db.execute(
            "SELECT * FROM obligations WHERE obligation_id=?", (obligation_id,)
        ).fetchone())

    def create_instruction(self, payment_id: str, obligation_id: str) -> dict[str, Any]:
        """Create exactly one stable instruction for an obligation; never submits it.

        BEGIN IMMEDIATE serializes competing writers before they inspect the
        obligation, so concurrent callers cannot both pass a read-then-write
        check and create separate instructions for one obligation.
        """
        if not payment_id:
            raise ValueError("PAYMENT_ID_REQUIRED")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            obligation = self.db.execute(
                "SELECT * FROM obligations WHERE obligation_id=?", (obligation_id,)
            ).fetchone()
            if not obligation:
                raise ValueError("OBLIGATION_NOT_FOUND")
            request = {
                "payment_id": payment_id,
                "obligation_id": obligation_id,
                "amount_minor": obligation["amount_minor"],
                "currency": obligation["currency"],
            }
            fingerprint = hashlib.sha256(
                json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            existing = self.db.execute(
                "SELECT * FROM payments WHERE payment_id=?", (payment_id,)
            ).fetchone()
            if existing:
                if existing["request_hash"] != fingerprint:
                    raise ValueError("PAYMENT_ID_CONFLICT")
                self.db.commit()
                return dict(existing)
            self.db.execute(
                """INSERT INTO payments
                   (payment_id, obligation_id, request_hash, amount_minor, currency, status)
                   VALUES (?, ?, ?, ?, ?, 'INSTRUCTION_CREATED')""",
                (payment_id, obligation_id, fingerprint, obligation["amount_minor"], obligation["currency"]),
            )
            self.db.execute(
                "UPDATE obligations SET status='PAYMENT_INSTRUCTED' WHERE obligation_id=?",
                (obligation_id,),
            )
            self._event(payment_id + ":INSTRUCTION", payment_id, "INSTRUCTION_CREATED", request)
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            self.db.rollback()
            raise ValueError("OBLIGATION_ALREADY_HAS_PAYMENT_INSTRUCTION") from exc
        except Exception:
            self.db.rollback()
            raise
        return dict(self.db.execute(
            "SELECT * FROM payments WHERE payment_id=?", (payment_id,)
        ).fetchone())

    def record_provider_ack(
        self, payment_id: str, event_id: str, acknowledgement: str, provider_reference: str | None = None
    ) -> dict[str, Any]:
        """Record provider response; acknowledgement is not settlement evidence."""
        allowed = {"ACCEPTED", "DECLINED", "UNKNOWN"}
        if acknowledgement not in allowed:
            raise ValueError("INVALID_PROVIDER_ACKNOWLEDGEMENT")
        payment = self._payment(payment_id)
        evidence = {"acknowledgement": acknowledgement, "provider_reference": provider_reference}
        self._event(event_id, payment_id, "PROVIDER_ACKNOWLEDGEMENT", evidence)
        if acknowledgement == "ACCEPTED":
            status = "PROVIDER_ACCEPTED_SETTLEMENT_UNCONFIRMED"
        elif acknowledgement == "DECLINED":
            status = "PROVIDER_DECLINED"
        else:
            status = "UNKNOWN_PENDING_RECONCILIATION"
        if payment["status"] == "SETTLED":
            self.db.rollback()
            raise ValueError("PAYMENT_ALREADY_SETTLED")
        self.db.execute(
            "UPDATE payments SET status=?, provider_reference=? WHERE payment_id=?",
            (status, provider_reference, payment_id),
        )
        self.db.commit()
        return self.get_payment(payment_id)

    def record_settlement(
        self, payment_id: str, event_id: str, settlement_reference: str, *, amount_minor: int, currency: str
    ) -> dict[str, Any]:
        """Accept explicit matching settlement evidence, not a mere provider ACK."""
        payment = self._payment(payment_id)
        if not settlement_reference:
            raise ValueError("SETTLEMENT_REFERENCE_REQUIRED")
        if amount_minor != payment["amount_minor"] or currency != payment["currency"]:
            raise ValueError("SETTLEMENT_AMOUNT_OR_CURRENCY_MISMATCH")
        evidence = {
            "settlement_reference": settlement_reference,
            "amount_minor": amount_minor,
            "currency": currency,
        }
        self._event(event_id, payment_id, "SETTLEMENT_EVIDENCE", evidence)
        if payment["status"] == "SETTLED":
            if payment["settlement_reference"] == settlement_reference:
                self.db.commit()
                return self.get_payment(payment_id)
            self.db.rollback()
            raise ValueError("CONFLICTING_SETTLEMENT_EVIDENCE")
        self.db.execute(
            "UPDATE payments SET status='SETTLED', settlement_reference=? WHERE payment_id=?",
            (settlement_reference, payment_id),
        )
        self.db.execute(
            "UPDATE obligations SET status='SETTLED' WHERE obligation_id=?",
            (payment["obligation_id"],),
        )
        self.db.commit()
        return self.get_payment(payment_id)

    def record_reconciliation(
        self, payment_id: str, event_id: str, finding: str, provider_reference: str | None = None
    ) -> dict[str, Any]:
        """Reconciliation may establish settled/not-settled/unknown; it never resubmits."""
        if finding not in {"SETTLED", "NOT_SETTLED", "UNKNOWN"}:
            raise ValueError("INVALID_RECONCILIATION_FINDING")
        payment = self._payment(payment_id)
        evidence = {"finding": finding, "provider_reference": provider_reference}
        self._event(event_id, payment_id, "RECONCILIATION", evidence)
        if finding == "SETTLED" and payment["status"] != "SETTLED":
            self.db.rollback()
            raise ValueError("SETTLEMENT_EVIDENCE_REQUIRED")
        if payment["status"] == "SETTLED" and finding != "SETTLED":
            self.db.rollback()
            raise ValueError("RECONCILIATION_CONFLICTS_WITH_SETTLEMENT")
        if payment["status"] != "SETTLED":
            status = "RECONCILED_NOT_SETTLED" if finding == "NOT_SETTLED" else "UNKNOWN_PENDING_RECONCILIATION"
            self.db.execute(
                "UPDATE payments SET status=?, last_reconciliation=? WHERE payment_id=?",
                (status, finding, payment_id),
            )
        else:
            self.db.execute(
                "UPDATE payments SET last_reconciliation=? WHERE payment_id=?",
                (finding, payment_id),
            )
        self.db.commit()
        return self.get_payment(payment_id)

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        return dict(self._payment(payment_id))

    def history(self, payment_id: str) -> list[dict[str, Any]]:
        return [
            {**dict(row), "evidence": json.loads(row["evidence_json"])}
            for row in self.db.execute(
                "SELECT * FROM payment_events WHERE payment_id=? ORDER BY rowid", (payment_id,)
            )
        ]

    def _payment(self, payment_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
        if not row:
            raise ValueError("PAYMENT_NOT_FOUND")
        return row

    def _event(self, event_id: str, payment_id: str, event_type: str, evidence: dict[str, Any]) -> None:
        if not event_id:
            raise ValueError("EVENT_ID_REQUIRED")
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        existing = self.db.execute(
            "SELECT payment_id, event_type, evidence_json FROM payment_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if existing:
            if (existing["payment_id"], existing["event_type"], existing["evidence_json"]) != (
                payment_id, event_type, payload
            ):
                raise ValueError("EVENT_ID_CONFLICT")
            return
        self.db.execute(
            "INSERT INTO payment_events VALUES (?, ?, ?, ?)",
            (event_id, payment_id, event_type, payload),
        )
