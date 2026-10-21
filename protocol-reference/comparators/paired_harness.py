"""Shared scenario harness for the KIN-SYS slice and conventional SQLite baseline.

The harness normalizes observable results only; each implementation keeps its
own schema and internal execution path.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Callable

from transition import EventIdConflict, ProtocolStore
from comparators.conventional_sqlite import ConventionalSQLiteBaseline


def _request(**overrides: Any) -> dict[str, str]:
    request = {
        "event_id": "E-001",
        "process_id": "P-001",
        "actor_id": "AUTHORITY-001",
        "event_type": "ACTIVATE",
        "rule_set": "LICENCE-RULES",
        "rule_version": "1.0",
    }
    request.update(overrides)
    return request


class _KinAdapter:
    def __init__(self, database: str):
        self.store = ProtocolStore(database=database)
        self.store.create_process(process_id="P-001")

    def execute(self, request: dict[str, str], *, fail_before_commit: bool = False):
        result = self.store.execute(request, fail_before_commit=fail_before_commit)
        return {
            "status": result.status,
            "reason": result.reason,
            "duplicate": result.duplicate,
        }

    def conflict_exception(self):
        return EventIdConflict

    def snapshot(self) -> dict[str, Any]:
        process = self.store.process("P-001")
        return {
            "state": process["state"],
            "version": process["state_version"],
            "history_count": len(self.store.history("P-001")),
            "event_outcome": (
                self.store.event("E-001")["outcome"]
                if self.store.event("E-001") else None
            ),
        }

    def close(self):
        self.store.close()


class _ConventionalAdapter:
    def __init__(self, database: str):
        self.store = ConventionalSQLiteBaseline(database=database)
        self.store.create_object("P-001")

    def execute(self, request: dict[str, str], *, fail_before_commit: bool = False):
        result = self.store.execute(request, fail_before_commit=fail_before_commit)
        return {
            "status": result["status"],
            "reason": result["reason"],
            "duplicate": result["duplicate"],
        }

    def conflict_exception(self):
        return ValueError

    def snapshot(self) -> dict[str, Any]:
        obj = self.store.object("P-001")
        operation = self.store.operation("E-001")
        return {
            "state": obj["state"],
            "version": obj["version"],
            "history_count": len(self.store.history("P-001")),
            "event_outcome": operation["status"] if operation else None,
        }

    def close(self):
        self.store.close()


ADAPTERS: dict[str, Callable[[str], Any]] = {
    "kin-sys-sqlite": _KinAdapter,
    "conventional-sqlite": _ConventionalAdapter,
}


def run_scenario(implementation: str, scenario: str) -> dict[str, Any]:
    """Run one shared scenario and return a normalized observable vector."""
    if implementation not in ADAPTERS:
        raise ValueError(f"UNKNOWN_IMPLEMENTATION:{implementation}")
    if scenario not in {
        "accepted", "duplicate", "conflicting-id", "wrong-authority",
        "rule-version-mismatch", "precommit-failure",
    }:
        raise ValueError(f"UNKNOWN_SCENARIO:{scenario}")

    with tempfile.TemporaryDirectory() as directory:
        database = os.path.join(directory, "comparison.sqlite")
        adapter = ADAPTERS[implementation](database)
        request = _request()
        outcome: dict[str, Any] = {"status": None, "reason": None, "duplicate": False}
        try:
            if scenario == "duplicate":
                adapter.execute(request)
                outcome = adapter.execute(request)
            elif scenario == "conflicting-id":
                adapter.execute(request)
                try:
                    adapter.execute(_request(actor_id="OTHER-ACTOR"))
                    outcome = {"status": "UNEXPECTED_ACCEPT", "reason": None, "duplicate": False}
                except adapter.conflict_exception():
                    outcome = {"status": "CONFLICT", "reason": "EVENT_ID_CONFLICT", "duplicate": False}
            elif scenario == "wrong-authority":
                outcome = adapter.execute(_request(actor_id="OTHER-ACTOR"))
            elif scenario == "rule-version-mismatch":
                outcome = adapter.execute(_request(rule_version="0.9"))
            elif scenario == "precommit-failure":
                try:
                    adapter.execute(request, fail_before_commit=True)
                    outcome = {"status": "UNEXPECTED_ACCEPT", "reason": None, "duplicate": False}
                except RuntimeError as error:
                    if "INJECTED_FAILURE_BEFORE_COMMIT" not in str(error):
                        raise
                    outcome = {"status": "INTERRUPTED", "reason": "PRECOMMIT_FAILURE", "duplicate": False}
            else:
                outcome = adapter.execute(request)

            snapshot = adapter.snapshot()
            return {
                "schema_version": "0.1",
                "implementation": implementation,
                "scenario": scenario,
                **outcome,
                **snapshot,
            }
        finally:
            adapter.close()


def compare_scenario(scenario: str) -> dict[str, Any]:
    """Return both normalized vectors and their externally visible differences."""
    left = run_scenario("kin-sys-sqlite", scenario)
    right = run_scenario("conventional-sqlite", scenario)
    fields = ("status", "reason", "duplicate", "state", "version", "history_count", "event_outcome")
    differences = {
        field: {"kin_sys": left[field], "conventional": right[field]}
        for field in fields if left[field] != right[field]
    }
    return {
        "scenario": scenario,
        "equivalent_on_observed_fields": not differences,
        "differences": differences,
        "results": {"kin_sys": left, "conventional": right},
    }
