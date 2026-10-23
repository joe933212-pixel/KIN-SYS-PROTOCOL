# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""CEE-owned case coordinator for a synthetic institutional/payment workflow.

Research prototype only. CEE, IVM, institutional workflow and payment records use
separate SQLite stores. This is a recoverable local orchestration model, not a
distributed transaction or production payment service.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from integrated_demo import DEMO_RULE_VERSION, DeterministicMinahAdapter, SyntheticTaxRules, SimulatedPaymentProvider
from payment_lifecycle import PaymentLifecycle
from protocol_pipeline import AUTHORITY_ID, RULE_SET, RULE_VERSION, CEEJournal, KinSysProtocolPipeline
from transition import ProtocolStore, ProcessNotFound
from workflows.institutional_workflow import InstitutionalWorkflow


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class CaseConflict(ValueError):
    """Operation ID was reused with different case content."""


class CEECaseCoordinator:
    """Orchestrate one synthetic case through CEE, MINAH, institutions, IVM and payment."""

    def __init__(
        self,
        *,
        cee_database: str = ":memory:",
        ivm_database: str = ":memory:",
        workflow_database: str = ":memory:",
        payment_database: str = ":memory:",
        minah_adapter: Any | None = None,
        authority_active: bool = True,
    ):
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
        self.ivm = ProtocolStore(ivm_database)
        self.workflow = InstitutionalWorkflow(workflow_database)
        self.payments = PaymentLifecycle(payment_database)
        self.minah = minah_adapter or DeterministicMinahAdapter()
        self.tax = SyntheticTaxRules()
        self.provider = SimulatedPaymentProvider()
        self.authority_active = authority_active

    def close(self) -> None:
        self.cee_db.close()
        self.ivm.close()
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
                self._event(operation_id, "CEE", "REQUESTED", "ACCEPTED", {"request_digest": digest})
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
        self._set_phase(operation_id, "ADMITTED", "ACCEPTED", {"schema_validated": True})
        self._set_phase(operation_id, "CONTEXT_PINNED", "ACCEPTED", {
            "jurisdiction": request["jurisdiction"],
            "rule_version": DEMO_RULE_VERSION,
            "evidence_digest": _digest(evidence),
        })

        state = self._case_row(operation_id)
        minah = state.get("minah")
        if minah is None:
            raw = self.minah.review(evidence)
            if not isinstance(raw, dict) or raw.get("may_authorise") is not False or raw.get("status") not in {"ADVISORY_ONLY", "NEEDS_INFORMATION"}:
                return self._finish(operation_id, "REJECTED", "REJECTED", {"reason": "MINAH_CONTRACT_VIOLATION", "minah_disposition": {"status": "INVALID_DISPOSITION", "may_authorise": False}, "funds_moved": False})
            minah = raw
            self._event(operation_id, "MINAH", "DISPOSITION_RECORDED", minah["status"], {
                "advisory_only": True, "may_authorise": False,
                "model_version": minah.get("model_version"),
                "source_refs": minah.get("source_refs", []),
                "uncertainty": minah.get("uncertainty"),
            })
            self._save_component(operation_id, "minah", minah)
        if minah["status"] == "NEEDS_INFORMATION":
            return self._finish(operation_id, "DEFERRED", "DEFERRED", {
                "reason": "MINAH_REQUIRES_ADDITIONAL_EVIDENCE", "minah_disposition": minah, "institutional_decisions": None,
                "payment_lifecycle": None, "funds_moved": False,
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
        self._event(operation_id, "IVM", "ECONOMIC_GATE", decision.status, ivm_result)
        if decision.status != "ACCEPTED":
            return self._finish(operation_id, "REJECTED", "IVM_ECONOMIC_GATE_REJECTED", {
                "minah_disposition": minah, "institutional_decisions": stages,
                "ivm_decision": ivm_result, "obligation": None, "payment_lifecycle": None,
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
            "ivm_decision": ivm_result, "assessment": assessment,
            "obligation": obligation, "payment_lifecycle": payment_result,
            "funds_moved": False, "production_ready": False,
        })

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
            "REQUESTED": 0, "ADMITTED": 1, "CONTEXT_PINNED": 2,
            "FINALIZED": 3, "DEFERRED": 3, "REJECTED": 3,
            "PENDING_RECONCILIATION": 3, "PENDING_PAYMENT_RESOLUTION": 3,
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
        stored_result = {**result, "operation_id": operation_id, "status": status}
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
        return [{
            "sequence": event["sequence"], "component": event["component"],
            "phase": event["phase"], "status": event["status"], "detail": event["detail"],
        } for event in events]

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
