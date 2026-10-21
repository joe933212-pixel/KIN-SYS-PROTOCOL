# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Shared scenario harness for the KIN-SYS slice and conventional SQLite baseline.

The harness normalizes observable results only; each implementation keeps its
own schema and internal execution path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import concurrent.futures
import threading
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
    def __init__(self, database: str, *, initialize: bool = True):
        self.store = ProtocolStore(database=database)
        if initialize:
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
    def __init__(self, database: str, *, initialize: bool = True):
        self.store = ConventionalSQLiteBaseline(database=database)
        if initialize:
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
        "rule-version-mismatch", "precommit-failure", "concurrent-identical",
        "process-death-before-commit", "postcommit-before-ack",
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
            elif scenario == "concurrent-identical":
                adapter.close()
                barrier = threading.Barrier(2)

                def submit():
                    worker = ADAPTERS[implementation](database, initialize=False)
                    try:
                        barrier.wait(timeout=5)
                        result = worker.execute(request)
                        return {
                            "status": result.status if hasattr(result, "status") else result["status"],
                            "duplicate": result.duplicate if hasattr(result, "duplicate") else result["duplicate"],
                        }
                    finally:
                        worker.close()

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    concurrent_results = list(pool.map(lambda _: submit(), range(2)))
                outcome = {
                    "status": "ACCEPTED" if all(item["status"] == "ACCEPTED" for item in concurrent_results) else "DIVERGENT",
                    "reason": None,
                    "duplicate": sum(bool(item["duplicate"]) for item in concurrent_results) == 1,
                    "accepted_count": sum(item["status"] == "ACCEPTED" for item in concurrent_results),
                    "duplicate_count": sum(bool(item["duplicate"]) for item in concurrent_results),
                }
                adapter = ADAPTERS[implementation](database, initialize=False)
            elif scenario == "precommit-failure":
                try:
                    adapter.execute(request, fail_before_commit=True)
                    outcome = {"status": "UNEXPECTED_ACCEPT", "reason": None, "duplicate": False}
                except RuntimeError as error:
                    if "INJECTED_FAILURE_BEFORE_COMMIT" not in str(error):
                        raise
                    outcome = {"status": "INTERRUPTED", "reason": "PRECOMMIT_FAILURE", "duplicate": False}
            elif scenario in {"process-death-before-commit", "postcommit-before-ack"}:
                adapter.close()
                marker = os.path.join(directory, "crash-point.marker")
                child_script = r"""
import os, sys, time
sys.path.insert(0, os.path.join(os.getcwd(), 'protocol-reference'))
from comparators.paired_harness import ADAPTERS, _request
implementation, database, marker, mode = sys.argv[1:5]
store = ADAPTERS[implementation](database, initialize=False)
if mode == "before":
    trigger_table = "transition_history" if implementation == "kin-sys-sqlite" else "baseline_audit"
    def pause_at_shared_crash_point():
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("inside transaction before history/audit insert")
        time.sleep(60)
    store.store.db.create_function("pause_at_shared_crash_point", 0, pause_at_shared_crash_point)
    store.store.db.execute(
        f"CREATE TRIGGER shared_pause_before_history BEFORE INSERT ON {trigger_table} "
        "BEGIN SELECT pause_at_shared_crash_point(); END"
    )
    store.store.db.commit()
    store.execute(_request())
else:
    store.execute(_request())
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("committed; acknowledgement withheld")
    time.sleep(60)
"""
                mode = "before" if scenario == "process-death-before-commit" else "after"
                child = subprocess.Popen(
                    [sys.executable, "-c", child_script, implementation, database, marker, mode],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                deadline = time.monotonic() + 8
                try:
                    while time.monotonic() < deadline and not os.path.exists(marker):
                        if child.poll() is not None:
                            raise RuntimeError(
                                f"CHILD_EXITED_BEFORE_CRASH_MARKER:{implementation}:{child.returncode}"
                            )
                        time.sleep(0.01)
                    if not os.path.exists(marker):
                        raise RuntimeError(f"CRASH_MARKER_TIMEOUT:{implementation}:{scenario}")
                    child.terminate()
                    child.wait(timeout=5)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait(timeout=5)
                adapter = ADAPTERS[implementation](database, initialize=False)
                if mode == "before":
                    outcome = {"status": "INTERRUPTED", "reason": "PROCESS_TERMINATED_BEFORE_COMMIT", "duplicate": False}
                else:
                    retry_result = adapter.execute(request)
                    retry_duplicate = (
                        retry_result.duplicate if hasattr(retry_result, "duplicate")
                        else retry_result["duplicate"]
                    )
                    outcome = {
                        "status": "COMMITTED_ACK_LOST",
                        "reason": "POSTCOMMIT_RETRY",
                        "duplicate": False,
                        "retry_duplicate": bool(retry_duplicate),
                    }
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
    fields = ("status", "reason", "duplicate", "retry_duplicate", "state", "version", "history_count", "event_outcome", "accepted_count", "duplicate_count")
    differences = {
        field: {"kin_sys": left[field], "conventional": right[field]}
        for field in fields if left.get(field) != right.get(field)
    }
    return {
        "scenario": scenario,
        "equivalent_on_observed_fields": not differences,
        "differences": differences,
        "results": {"kin_sys": left, "conventional": right},
    }


def run_all_scenarios() -> dict[str, Any]:
    scenarios = (
        "accepted", "duplicate", "conflicting-id", "wrong-authority",
        "rule-version-mismatch", "precommit-failure", "concurrent-identical",
        "process-death-before-commit", "postcommit-before-ack",
    )
    comparisons = [compare_scenario(scenario) for scenario in scenarios]
    return {
        "schema_version": "0.1",
        "all_equivalent_on_observed_fields": all(
            result["equivalent_on_observed_fields"] for result in comparisons
        ),
        "comparisons": comparisons,
    }


if __name__ == "__main__":
    print(json.dumps(run_all_scenarios(), sort_keys=True, indent=2))
