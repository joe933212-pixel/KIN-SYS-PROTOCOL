# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""CEE case coordinator for a synthetic institutional/payment workflow.

MINAH review precedes CEE admission. The private execution runtime shares the
CEE case-journal connection; institution and payment adapters retain their own
stores. This is not a distributed transaction or production payment service.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from integrated_demo import DEMO_RULE_VERSION, DeterministicMinahAdapter, SyntheticTaxRules, SimulatedPaymentProvider
from payment_lifecycle import PaymentLifecycle
from protocol_pipeline import AUTHORITY_ID, RULE_SET, RULE_VERSION, KinSysProtocolPipeline
from transition import ProtocolStore, ProcessNotFound
from workflows.institutional_workflow import InstitutionalWorkflow


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class CaseConflict(ValueError):
    """Operation ID was reused with different case content."""


class CEECaseCoordinator:
    """Orchestrate one synthetic case through MINAH, CEE and final disposition."""

    def __init__(
        self,
        *,
        cee_database: str = ":memory:",
        ivm_database: str | None = None,
        workflow_database: str = ":memory:",
        payment_database: str = ":memory:",
        minah_adapter: Any | None = None,
        authority_active: bool = True,
    ):
        if ivm_database is not None and ivm_database != cee_database:
            raise ValueError("SPLIT_CASE_RUNTIME_STORAGE_NO_LONGER_SUPPORTED")
        self.cee_db = sqlite3.connect(cee_database, timeout=10.0)
        self.cee_db.row_factory = sqlite3.Row
        self.cee_db.execute("PRAGMA busy_timeout=10000")
        self.cee_db.executescript("""
            CREATE TABLE IF NOT EXISTS cee_cases (
                operation_id TEXT PRIMARY KEY,
                request_digest TEXT NOT NULL,
                request_json TEXT NOT NULL,
                phase TEXT NOT NULL,
                minah_json TEXT,
                result_json TEXT
            );
            CREATE TABLE IF NOT EXISTS cee_case_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL REFERENCES cee_cases(operation_id),
                component TEXT NOT NULL,
                phase TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL
            );
        """)
        columns = {row["name"] for row in self.cee_db.execute("PRAGMA table_info(cee_cases)")}
        if "minah_json" not in columns:
            self.cee_db.execute("ALTER TABLE cee_cases ADD COLUMN minah_json TEXT")
        # One durable submission intent per operation prevents concurrent retries
        # from both reaching a provider. Existing duplicates require manual review.
        self.cee_db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS one_payment_submission_intent
               ON cee_case_events(operation_id)
               WHERE component='PAYMENT' AND phase='SUBMISSION_INTENT'"""
        )
        self.cee_db.commit()
        self.ivm = ProtocolStore(connection=self.cee_db)
        self.workflow = InstitutionalWorkflow(workflow_database)
        self.payments = PaymentLifecycle(payment_database)
        self.minah = minah_adapter or DeterministicMinahAdapter()
        self.tax = SyntheticTaxRules()
        self.provider = SimulatedPaymentProvider()
        self.authority_active = authority_active

    def close(self) -> None:
        self.ivm.close()
        self.cee_db.close()
        self.workflow.close()
        self.payments.close()

    def inspect(self, operation_id: str) -> dict[str, Any] | None:
        row = self.cee_db.execute(
            "SELECT * FROM cee_cases WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None:
            return None
        events = [
            {**dict(event), "detail": json.loads(event["detail_json"])}
            for event in self.cee_db.execute(
                "SELECT * FROM cee_case_events WHERE operation_id=? ORDER BY sequence",
                (operation_id,),
            )
        ]
        return {
            "operation_id": operation_id,
            "phase": row["phase"],
            "resumable": row["phase"] not in {"FINALIZED", "DEFERRED", "REJECTED"},
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "events": events,
        }

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        self._validate(request)
        operation_id = request["operation_id"]
        digest = _digest(request)
        self.cee_db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.cee_db.execute(
                "SELECT * FROM cee_cases WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if prior:
                if prior["request_digest"] != digest:
                    raise CaseConflict("OPERATION_ID_REUSED_WITH_DIFFERENT_CASE")
                self.cee_db.commit()
            else:
                self.cee_db.execute(
                    "INSERT INTO cee_cases(operation_id,request_digest,request_json,phase) VALUES(?,?,?,'REQUESTED')",
                    (operation_id, digest, _canonical(request)),
                )
                self.cee_db.commit()
                self._event(operation_id, "MINAH", "INGRESS_RECEIVED", "RECEIVED", {"request_digest": digest})
        except Exception:
            if self.cee_db.in_transaction:
                self.cee_db.rollback()
            raise

        saved = self.inspect(operation_id)
        assert saved is not None
        if saved["result"] is not None:
            return {**saved["result"], "duplicate": True, "cee_events": saved["events"], "component_trace": self._trace(saved["events"])}

        case_id = request["case_id"]
        evidence = request["evidence"]

        # MINAH is the first protocol-facing component. REQUESTED is only a
        # durable ingress receipt; no CEE admission/context is created yet.
        state = self._case_row(operation_id)
        minah = state.get("minah")
        if minah is None:
            raw = self.minah.review(evidence)
            contract_error = KinSysProtocolPipeline._minah_contract_error(raw)
            if contract_error:
                minah = {
                    "status": "INVALID_DISPOSITION", "missing_fields": [],
                    "source_refs": [], "uncertainty": "HIGH",
                    "may_authorise": False, "model_version": "UNTRUSTED_OUTPUT",
                    "contract_error": contract_error,
                }
            else:
                minah = raw
            self._event(operation_id, "MINAH", "DISPOSITION_RECORDED", minah["status"], {
                "advisory_only": True, "may_authorise": False,
                "model_version": minah.get("model_version"),
                "source_refs": minah.get("source_refs", []),
                "uncertainty": minah.get("uncertainty"),
                "contract_error": minah.get("contract_error"),
            })
            self._save_component(operation_id, "minah", minah)

        if minah.get("status") == "INVALID_DISPOSITION":
            self._set_phase(operation_id, "MINAH_REVIEWED", "REJECTED", {
                "cee_admission": "NOT_GRANTED",
                "reason": "MINAH_CONTRACT_VIOLATION",
            })
            return self._finish(operation_id, "REJECTED", "REJECTED", {
                "reason": "MINAH_CONTRACT_VIOLATION", "minah_disposition": minah,
                "institutional_decisions": None, "execution_result": None,
                "payment_lifecycle": None, "funds_moved": False,
            })

        if minah["status"] == "NEEDS_INFORMATION":
            self._set_phase(operation_id, "MINAH_REVIEWED", "DEFERRED", {
                "cee_admission": "NOT_GRANTED",
                "reason": "MINAH_REQUIRES_ADDITIONAL_EVIDENCE",
            })
            return self._finish(operation_id, "DEFERRED", "DEFERRED", {
                "reason": "MINAH_REQUIRES_ADDITIONAL_EVIDENCE",
                "minah_disposition": minah, "institutional_decisions": None,
                "execution_result": None, "payment_lifecycle": None, "funds_moved": False,
            })

        # Server-configured synthetic checks run before CEE admission. These are
        # not identity verification against an authorised external registry.
        pre_admission_error = self._pre_admission_error(request)
        if pre_admission_error:
            self._set_phase(operation_id, "MINAH_REVIEWED", "REJECTED", {
                "cee_admission": "NOT_GRANTED", "reason": pre_admission_error,
            })
            return self._finish(operation_id, "REJECTED", "REJECTED", {
                "reason": pre_admission_error, "minah_disposition": minah,
                "institutional_decisions": None, "execution_result": None,
                "payment_lifecycle": None, "funds_moved": False,
            })

        self._set_phase(operation_id, "MINAH_REVIEWED", "REVIEWED", {
            "cee_admission": "NOT_YET_GRANTED", "may_authorise": False,
        })
        self._set_phase(operation_id, "ADMITTED", "ACCEPTED", {
            "schema_validated": True, "minah_review_pinned": True,
        })
        self._set_phase(operation_id, "CONTEXT_PINNED", "ACCEPTED", {
            "jurisdiction": request["jurisdiction"],
            "rule_version": DEMO_RULE_VERSION,
            "evidence_digest": _digest(evidence),
        })

        self.workflow.create_case(
            case_id, origin_authority="ORIGIN-A", destination_authority="DESTINATION-B",
            origin_rule_version=DEMO_RULE_VERSION, destination_rule_version=DEMO_RULE_VERSION,
        )
        stages = []
        for stage, actor, decision in (
            ("ORIGIN", request["origin_actor"], request["origin_decision"]),
            ("DESTINATION", request["destination_actor"], request["destination_decision"]),
        ):
            if stage == "DESTINATION" and (not stages or stages[0]["decision"] != "APPROVE" or stages[0]["status"] != "ACCEPTED"):
                self._event(operation_id, "INSTITUTIONAL", stage, "NOT_ATTEMPTED", {"reason": "ORIGIN_NOT_APPROVED"})
                stages.append({"stage": stage, "status": "NOT_ATTEMPTED", "decision": None, "reason": "ORIGIN_NOT_APPROVED"})
                break
            state = self.workflow.stage(case_id, stage)
            if stage == "ORIGIN" and request["revoke_origin_before_decision"]:
                self.workflow.set_authority_active(case_id, "ORIGIN", False)
            # A replay of a previously accepted stage is safe only if current authority
            # and rule state still match the recorded approval.
            event_id = f"{operation_id}:{stage}:DECISION:1"
            result = self.workflow.decide(
                event_id=event_id, consignment_id=case_id, stage=stage,
                actor_id=actor, decision=decision, rule_version=DEMO_RULE_VERSION,
            )
            current = self.workflow.stage(case_id, stage)
            history = self.workflow.history(case_id)
            valid_history = any(
                h["stage"] == stage and h["new_outcome"] == "APPROVE"
                and h["event_id"] == event_id and h["actor_id"] == current["authority_id"]
                and h["authority_id"] == current["authority_id"]
                and h["rule_version"] == DEMO_RULE_VERSION
                for h in history
            )
            accepted = (
                result["status"] == "ACCEPTED" and current["authority_active"] == 1
                and current["rule_version"] == DEMO_RULE_VERSION
                and current["outcome"] == "APPROVE" and valid_history
            ) if decision == "APPROVE" else result["status"] == "ACCEPTED" and current["outcome"] == "REJECT"
            stage_result = {
                "stage": stage, "status": result["status"],
                "decision": current["outcome"], "reason": result.get("reason"),
                "duplicate": result.get("duplicate", False), "current_approval_valid": bool(accepted),
            }
            stages.append(stage_result)
            self._event(operation_id, "INSTITUTIONAL", stage, "ACCEPTED" if accepted else ("REJECTED" if result["status"] == "REJECTED" or current["outcome"] == "REJECT" else "DEFERRED"), stage_result)
            if not accepted:
                break

        if len(stages) < 2 or any(s["status"] != "ACCEPTED" or s["decision"] != "APPROVE" or not s.get("current_approval_valid") for s in stages):
            rejected = any(s["decision"] == "REJECT" or s["status"] == "REJECTED" for s in stages)
            status = "REJECTED" if rejected else "DEFERRED"
            reason = "INSTITUTIONAL_APPROVAL_REQUIRED"
            return self._finish(operation_id, status, status, {
                "reason": reason, "minah_disposition": minah, "institutional_decisions": stages,
                "obligation": None, "payment_lifecycle": None, "funds_moved": False,
            })

        # IVM transition is deliberately after both institutional decisions. It
        # gates economic preparation; it is not a substitute for either institution.
        process_id = f"{operation_id}:ECONOMIC-GATE"
        event_id = f"{operation_id}:IVM:ECONOMIC-GATE:1"
        try:
            self.ivm.process(process_id)
        except ProcessNotFound:
            self.ivm.create_process(
                process_id=process_id, initial_state="PENDING", authority_id=AUTHORITY_ID,
                authority_active=self.authority_active, rule_set=RULE_SET, rule_version=RULE_VERSION,
                eligible=evidence.get("commodity") != "SYNTHETIC_PROHIBITED_COMMODITY",
            )
        decision = self.ivm.execute({
            "event_id": event_id, "process_id": process_id, "actor_id": request["execution_actor"],
            "event_type": "ACTIVATE", "rule_set": RULE_SET, "rule_version": RULE_VERSION,
        })
        ivm_result = {
            "status": decision.status, "reason": decision.reason,
            "event_id": decision.event_id, "history_id": decision.history_id,
            "from_state": decision.from_state, "to_state": decision.to_state,
            "duplicate": decision.duplicate,
        }
        self._event(operation_id, "CEE", "EXECUTION_GATE", decision.status, ivm_result)
        if decision.status != "ACCEPTED":
            return self._finish(operation_id, "REJECTED", "CEE_EXECUTION_GATE_REJECTED", {
                "minah_disposition": minah, "institutional_decisions": stages,
                "execution_result": ivm_result, "obligation": None, "payment_lifecycle": None,
                "funds_moved": False,
            })

        assessment = self.tax.assess(request["declared_value_minor"], exempt=request["exempt"])
        self._event(operation_id, "RULES", "ASSESSMENT", "ACCEPTED", {
            **assessment, "status": assessment["status"],
        })
        obligation = None
        payment_result = None
        if assessment["assessed_amount_ugx"] > 0:
            obligation_id = f"{operation_id}:OBLIGATION:1"
            payment_id = f"{operation_id}:PAYMENT:1"
            obligation = self.payments.create_obligation(
                obligation_id, assessment["assessed_amount_ugx"], "UGX", f"{operation_id}:ASSESSMENT:1"
            )
            instruction = self.payments.create_instruction(payment_id, obligation_id)
            self._event(operation_id, "PAYMENT", "INSTRUCTION_PREPARED", "ACCEPTED", {
                "payment_id": payment_id, "obligation_id": obligation_id,
                "instruction_status": instruction["status"], "provider_not_called_yet": True,
            })
            prior_outcome = self._find_event(operation_id, "PAYMENT", "PROVIDER_OUTCOME")
            prior_intent = self._find_event(operation_id, "PAYMENT", "SUBMISSION_INTENT")
            if prior_outcome:
                # A durable provider outcome is authoritative for retry; never resubmit.
                provider_status = prior_outcome["status"]
            elif prior_intent:
                # We cannot prove whether a provider received the request before a crash.
                # Fail closed into reconciliation instead of submitting a second time.
                provider_status = "UNKNOWN_PENDING_RECONCILIATION"
                self._event(operation_id, "PAYMENT", "PROVIDER_OUTCOME", provider_status, {
                    "payment_id": payment_id, "provider": "SIMULATED_ONLY",
                    "reason": "PRIOR_SUBMISSION_INTENT_WITHOUT_DURABLE_OUTCOME",
                    "funds_moved": False,
                })
            else:
                self._event(operation_id, "PAYMENT", "SUBMISSION_INTENT", "PENDING", {
                    "payment_id": payment_id, "idempotency_key": payment_id,
                    "provider": "SIMULATED_ONLY",
                })
                provider_result = self.provider.submit(
                    payment_id, assessment["assessed_amount_ugx"], outcome=request["payment_outcome"]
                )
                provider_status = provider_result["status"]
                self._event(operation_id, "PAYMENT", "PROVIDER_OUTCOME", provider_status, {
                    "payment_id": payment_id, "provider": "SIMULATED_ONLY", "funds_moved": False,
                })
            ack = {
                "SIMULATED_CONFIRMED": "ACCEPTED",
                "SIMULATED_DECLINED": "DECLINED",
                "UNKNOWN_PENDING_RECONCILIATION": "UNKNOWN",
            }.get(provider_status, "UNKNOWN")
            lifecycle = self.payments.record_provider_ack(
                payment_id, f"{payment_id}:ACK:1", ack,
                "SIMULATED-PROVIDER-REF" if ack == "ACCEPTED" else None,
            )
            if provider_status == "SIMULATED_CONFIRMED":
                lifecycle = self.payments.record_settlement(
                    payment_id, f"{payment_id}:SETTLEMENT:1", f"SIMULATED-SETTLEMENT:{payment_id}",
                    amount_minor=assessment["assessed_amount_ugx"], currency="UGX",
                )
                payment_status = "SIMULATED_SETTLED"
            elif provider_status == "SIMULATED_DECLINED":
                payment_status = "SIMULATED_DECLINED"
            else:
                lifecycle = self.payments.record_reconciliation(
                    payment_id, f"{payment_id}:RECONCILIATION:1", "UNKNOWN", source_sequence=1,
                )
                payment_status = "UNKNOWN_PENDING_RECONCILIATION"
            payment_result = {
                "payment_id": payment_id, "obligation_id": obligation_id,
                "status": payment_status, "recorded_status": lifecycle["status"],
                "settlement_reference": lifecycle.get("settlement_reference"),
                "history": self.payments.history(payment_id), "funds_moved": False,
            }
            self._event(operation_id, "PAYMENT", "LIFECYCLE_RECORDED", payment_status, payment_result)
        else:
            payment_result = {"status": "NOT_REQUIRED_EXEMPT", "funds_moved": False}
            self._event(operation_id, "PAYMENT", "NOT_REQUIRED", "ACCEPTED", payment_result)

        if payment_result["status"] == "SIMULATED_SETTLED":
            final_status = "COMPLETED_WITH_SIMULATED_SETTLEMENT"
        elif payment_result["status"] == "NOT_REQUIRED_EXEMPT":
            final_status = "COMPLETED_WITH_SYNTHETIC_EXEMPTION"
        elif payment_result["status"] == "SIMULATED_DECLINED":
            final_status = "PENDING_PAYMENT_RESOLUTION"
        else:
            final_status = "PENDING_PAYMENT_RECONCILIATION"
        terminal_phase = "PENDING_RECONCILIATION" if final_status == "PENDING_PAYMENT_RECONCILIATION" else ("PENDING_PAYMENT_RESOLUTION" if final_status == "PENDING_PAYMENT_RESOLUTION" else "FINALIZED")
        return self._finish(operation_id, terminal_phase, final_status, {
            "minah_disposition": minah, "institutional_decisions": stages,
            "execution_result": ivm_result, "assessment": assessment,
            "obligation": obligation, "payment_lifecycle": payment_result,
            "funds_moved": False, "production_ready": False,
        })

    def record_settlement_evidence(
        self, operation_id: str, event_id: str, settlement_reference: str,
        *, amount_minor: int, currency: str,
    ) -> dict[str, Any]:
        """Record caller-supplied synthetic settlement evidence with replay recovery.

        PaymentLifecycle commits first in its own store. If the CEE journal write
        is interrupted, retrying the same event replays the identical payment
        event and repairs the CEE result/event without creating a second effect.
        This is not authenticated provider evidence or a cross-store transaction.
        """
        if not operation_id or not event_id:
            raise ValueError("SETTLEMENT_IDENTIFIERS_REQUIRED")
        if not isinstance(settlement_reference, str) or not settlement_reference.strip():
            raise ValueError("SETTLEMENT_REFERENCE_REQUIRED")
        if type(amount_minor) is not int or amount_minor < 0:
            raise ValueError("INVALID_SETTLEMENT_AMOUNT")
        if not isinstance(currency, str) or not currency:
            raise ValueError("INVALID_SETTLEMENT_CURRENCY")
        row = self.cee_db.execute(
            "SELECT phase, result_json FROM cee_cases WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None or not row["result_json"]:
            raise KeyError("CEE_CASE_RESULT_NOT_FOUND")
        result = json.loads(row["result_json"])
        payment_info = result.get("payment_lifecycle")
        obligation = result.get("obligation")
        if not isinstance(payment_info, dict) or not payment_info.get("payment_id"):
            raise ValueError("CASE_HAS_NO_SETTLEABLE_PAYMENT")
        if not isinstance(obligation, dict):
            raise ValueError("CASE_HAS_NO_PINNED_OBLIGATION")
        payment_id = payment_info["payment_id"]
        evidence = {
            "event_id": event_id, "settlement_reference": settlement_reference,
            "amount_minor": amount_minor, "currency": currency,
            "evidence_source": "CALLER_SUPPLIED_SYNTHETIC_UNAUTHENTICATED",
        }
        existing_event = None
        for prior in self.cee_db.execute(
            "SELECT detail_json FROM cee_case_events WHERE operation_id=? AND component='PAYMENT' AND phase='SETTLEMENT_EVIDENCE'",
            (operation_id,),
        ):
            previous = json.loads(prior["detail_json"])
            if previous.get("event_id") == event_id:
                if previous != evidence:
                    raise CaseConflict("SETTLEMENT_EVENT_ID_CONFLICT")
                existing_event = previous
                break

        # Validate the CEE-pinned obligation before touching the payment store.
        if amount_minor != obligation.get("amount_minor") or currency != obligation.get("currency"):
            raise ValueError("SETTLEMENT_DOES_NOT_MATCH_PINNED_OBLIGATION")
        # The payment lifecycle independently checks its own obligation and stable
        # event ID; retry repairs CEE after a split-store crash.
        lifecycle = self.payments.record_settlement(
            payment_id, event_id, settlement_reference,
            amount_minor=amount_minor, currency=currency,
        )
        payment_info = {
            **payment_info, "status": "COMPLETED_WITH_RECONCILIATION_CONFIRMED_SETTLEMENT",
            "recorded_status": lifecycle["status"],
            "settlement_reference": lifecycle.get("settlement_reference"),
            "history": self.payments.history(payment_id), "funds_moved": False,
        }
        final_status = "COMPLETED_WITH_RECONCILIATION_CONFIRMED_SETTLEMENT"
        result = {
            **result, "status": final_status, "final_decision": "PASSED",
            "payment_lifecycle": payment_info, "funds_moved": False,
        }
        self.cee_db.execute("BEGIN IMMEDIATE")
        try:
            self.cee_db.execute(
                "UPDATE cee_cases SET phase='FINALIZED', result_json=? WHERE operation_id=?",
                (_canonical(result), operation_id),
            )
            if existing_event is None:
                self.cee_db.execute(
                    "INSERT INTO cee_case_events(operation_id,component,phase,status,detail_json) VALUES(?,?,?,?,?)",
                    (operation_id, "PAYMENT", "SETTLEMENT_EVIDENCE", lifecycle["status"], _canonical(evidence)),
                )
            self.cee_db.commit()
        except Exception:
            self.cee_db.rollback()
            raise
        snapshot = self.inspect(operation_id)
        assert snapshot is not None
        return {
            **result, "operation_id": operation_id, "duplicate": existing_event is not None,
            "cee_events": snapshot["events"], "component_trace": self._trace(snapshot["events"]),
        }

    def reconcile(
        self, operation_id: str, event_id: str, finding: str,
        *, source_sequence: int, provider_reference: str | None = None,
    ) -> dict[str, Any]:
        """Record synthetic, source-sequenced reconciliation; never resubmit payment."""
        if not operation_id or not event_id:
            raise ValueError("RECONCILIATION_IDENTIFIERS_REQUIRED")
        if finding not in {"SETTLED", "NOT_SETTLED", "UNKNOWN"}:
            raise ValueError("INVALID_RECONCILIATION_FINDING")
        if type(source_sequence) is not int or source_sequence < 1:
            raise ValueError("INVALID_RECONCILIATION_SEQUENCE")
        row = self.cee_db.execute(
            "SELECT phase, result_json FROM cee_cases WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None or not row["result_json"]:
            raise KeyError("CEE_CASE_RESULT_NOT_FOUND")
        result = json.loads(row["result_json"])
        payment_info = result.get("payment_lifecycle")
        if not isinstance(payment_info, dict) or not payment_info.get("payment_id"):
            raise ValueError("CASE_HAS_NO_RECONCILABLE_PAYMENT")
        payment_id = payment_info["payment_id"]
        evidence = {
            "event_id": event_id, "finding": finding,
            "source_sequence": source_sequence, "provider_reference": provider_reference,
        }
        for prior in self.cee_db.execute(
            "SELECT detail_json FROM cee_case_events WHERE operation_id=? AND component='PAYMENT' AND phase='RECONCILIATION'",
            (operation_id,),
        ):
            previous = json.loads(prior["detail_json"])
            if previous.get("event_id") == event_id:
                comparable = {k: previous.get(k) for k in evidence}
                if comparable != evidence:
                    raise CaseConflict("RECONCILIATION_EVENT_ID_CONFLICT")
                snapshot = self.inspect(operation_id)
                assert snapshot is not None
                return {
                    **(snapshot["result"] or result), "duplicate": True,
                    "cee_events": snapshot["events"], "component_trace": self._trace(snapshot["events"]),
                }

        # Payment store commits independently. A retry with the same event ID
        # safely replays there if the CEE journal write was interrupted.
        lifecycle = self.payments.record_reconciliation(
            payment_id, event_id, finding, provider_reference, source_sequence=source_sequence,
        )
        if lifecycle["status"] == "SETTLED":
            final_status, phase = "COMPLETED_WITH_RECONCILIATION_CONFIRMED_SETTLEMENT", "FINALIZED"
        elif lifecycle["status"] == "RECONCILED_NOT_SETTLED":
            final_status, phase = "PENDING_PAYMENT_RESOLUTION", "PENDING_PAYMENT_RESOLUTION"
        else:
            final_status, phase = "PENDING_PAYMENT_RECONCILIATION", "PENDING_RECONCILIATION"
        payment_info = {
            **payment_info,
            "status": final_status if lifecycle["status"] == "SETTLED" else lifecycle["status"],
            "recorded_status": lifecycle["status"],
            "last_reconciliation": lifecycle.get("last_reconciliation"),
            "last_reconciliation_sequence": lifecycle.get("last_reconciliation_sequence"),
            "settlement_reference": lifecycle.get("settlement_reference"),
            "history": self.payments.history(payment_id),
            "funds_moved": False,
        }
        result = {
            **result, "status": final_status,
            "final_decision": "PASSED" if phase == "FINALIZED" else "DEFERRED",
            "payment_lifecycle": payment_info, "funds_moved": False,
        }
        self.cee_db.execute("BEGIN IMMEDIATE")
        try:
            self.cee_db.execute(
                "UPDATE cee_cases SET phase=?, result_json=? WHERE operation_id=?",
                (phase, _canonical(result), operation_id),
            )
            self.cee_db.execute(
                "INSERT INTO cee_case_events(operation_id,component,phase,status,detail_json) VALUES(?,?,?,?,?)",
                (operation_id, "PAYMENT", "RECONCILIATION", lifecycle["status"], _canonical(evidence)),
            )
            self.cee_db.commit()
        except Exception:
            self.cee_db.rollback()
            raise
        snapshot = self.inspect(operation_id)
        assert snapshot is not None
        return {
            **result, "operation_id": operation_id, "duplicate": False,
            "cee_events": snapshot["events"], "component_trace": self._trace(snapshot["events"]),
        }

    def _find_event(self, operation_id: str, component: str, phase: str) -> dict[str, Any] | None:
        row = self.cee_db.execute(
            """SELECT * FROM cee_case_events
               WHERE operation_id=? AND component=? AND phase=?
               ORDER BY sequence LIMIT 1""",
            (operation_id, component, phase),
        ).fetchone()
        if row is None:
            return None
        return {**dict(row), "detail": json.loads(row["detail_json"])}

    def _case_row(self, operation_id: str) -> dict[str, Any]:
        row = self.cee_db.execute("SELECT * FROM cee_cases WHERE operation_id=?", (operation_id,)).fetchone()
        result = dict(row)
        result["minah"] = json.loads(result["minah_json"]) if result.get("minah_json") else None
        return result

    def _save_component(self, operation_id: str, name: str, value: dict[str, Any]) -> None:
        # Pin MINAH output in the operation row so retries never silently rerun it.
        if name != "minah":
            raise ValueError("UNSUPPORTED_PINNED_COMPONENT")
        self.cee_db.execute(
            "UPDATE cee_cases SET minah_json=? WHERE operation_id=?",
            (_canonical(value), operation_id),
        )
        self.cee_db.commit()
        self._event(operation_id, "CEE_STATE", "PINNED_" + name.upper(), "RECORDED", value)

    def _event(self, operation_id: str, component: str, phase: str, status: str, detail: dict[str, Any]) -> None:
        self.cee_db.execute(
            "INSERT INTO cee_case_events(operation_id,component,phase,status,detail_json) VALUES(?,?,?,?,?)",
            (operation_id, component, phase, status, _canonical(detail)),
        )
        self.cee_db.commit()

    def _set_phase(self, operation_id: str, phase: str, status: str, detail: dict[str, Any]) -> None:
        order = {
            "REQUESTED": 0, "MINAH_REVIEWED": 1, "ADMITTED": 2, "CONTEXT_PINNED": 3,
            "FINALIZED": 4, "DEFERRED": 4, "REJECTED": 4,
            "PENDING_RECONCILIATION": 4, "PENDING_PAYMENT_RESOLUTION": 4,
        }
        row = self.cee_db.execute(
            "SELECT phase FROM cee_cases WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None:
            raise KeyError("CEE_CASE_NOT_FOUND")
        current = row["phase"]
        if order.get(phase, -1) <= order.get(current, -1):
            return
        self.cee_db.execute("UPDATE cee_cases SET phase=? WHERE operation_id=?", (phase, operation_id))
        self.cee_db.commit()
        self._event(operation_id, "CEE", phase, status, detail)

    def _finish(self, operation_id: str, phase: str, status: str, result: dict[str, Any]) -> dict[str, Any]:
        if phase == "REJECTED" or status == "REJECTED":
            final_decision = "REJECTED"
        elif phase == "FINALIZED" and status in {
            "COMPLETED_WITH_SIMULATED_SETTLEMENT",
            "COMPLETED_WITH_SYNTHETIC_EXEMPTION",
            "COMPLETED_WITH_RECONCILIATION_CONFIRMED_SETTLEMENT",
        }:
            final_decision = "PASSED"
        else:
            # Unknown, pending, or inconsistent terminal states fail closed.
            final_decision = "DEFERRED"
        stored_result = {
            **result, "operation_id": operation_id, "status": status,
            "final_decision": final_decision,
        }
        self.cee_db.execute("BEGIN IMMEDIATE")
        try:
            self.cee_db.execute(
                "UPDATE cee_cases SET phase=?, result_json=? WHERE operation_id=?",
                (phase, _canonical(stored_result), operation_id),
            )
            self.cee_db.execute(
                "INSERT INTO cee_case_events(operation_id,component,phase,status,detail_json) VALUES(?,?,?,?,?)",
                (operation_id, "CEE", "FINAL_RESULT", status, _canonical({"status": status})),
            )
            self.cee_db.commit()
        except Exception:
            self.cee_db.rollback()
            raise
        snapshot = self.inspect(operation_id)
        assert snapshot is not None
        return {
            **stored_result, "duplicate": False, "cee_events": snapshot["events"],
            "component_trace": self._trace(snapshot["events"]),
        }

    def _trace(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expose MINAH -> CEE; retain internal stages as CEE trace metadata."""
        trace = []
        for event in events:
            original_component = event["component"]
            component = "MINAH" if original_component == "MINAH" else "CEE"
            detail = dict(event["detail"])
            if original_component not in {"MINAH", "CEE"}:
                detail["internal_stage"] = original_component
            trace.append({
                "sequence": event["sequence"], "component": component,
                "phase": event["phase"], "status": event["status"], "detail": detail,
            })
        return trace

    def _pre_admission_error(self, request: dict[str, Any]) -> str | None:
        if request["execution_actor"] != AUTHORITY_ID:
            return "AUTHORITY_MISMATCH"
        if not self.authority_active:
            return "AUTHORITY_REVOKED"
        if request["evidence"].get("commodity") == "SYNTHETIC_PROHIBITED_COMMODITY":
            return "RULE_REJECTED"
        return None

    @staticmethod
    def _validate(request: dict[str, Any]) -> None:
        required = {
            "operation_id", "case_id", "jurisdiction", "evidence",
            "origin_actor", "destination_actor", "execution_actor",
            "origin_decision", "destination_decision", "declared_value_minor",
            "payment_outcome", "exempt", "revoke_origin_before_decision",
        }
        if not isinstance(request, dict) or set(request) != required:
            raise ValueError("INVALID_CEE_CASE_FIELDS")
        for field in ("operation_id", "case_id", "jurisdiction", "origin_actor", "destination_actor", "execution_actor"):
            if not isinstance(request[field], str) or not request[field]:
                raise ValueError("INVALID_CEE_CASE_FIELD:" + field)
        if not isinstance(request["evidence"], dict):
            raise ValueError("EVIDENCE_MUST_BE_OBJECT")
        if request["origin_decision"] not in {"APPROVE", "REJECT"} or request["destination_decision"] not in {"APPROVE", "REJECT"}:
            raise ValueError("INVALID_INSTITUTIONAL_DECISION")
        if type(request["declared_value_minor"]) is not int or request["declared_value_minor"] < 0:
            raise ValueError("INVALID_DECLARED_VALUE")
        if request["payment_outcome"] not in {"CONFIRMED", "DECLINED", "TIMEOUT_UNKNOWN"}:
            raise ValueError("INVALID_PAYMENT_OUTCOME")
        if type(request["exempt"]) is not bool or type(request["revoke_origin_before_decision"]) is not bool:
            raise ValueError("INVALID_BOOLEAN_FIELD")
