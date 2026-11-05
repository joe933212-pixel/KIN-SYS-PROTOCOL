# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""SQLite recovery snapshot-set capture and verification tests."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol_pipeline import KinSysProtocolPipeline
from recovery_snapshots import create_recovery_snapshot_set, verify_recovery_snapshot_set


def request():
    return {
        "operation_id": "SNAPSHOT-OP-001",
        "event_id": "SNAPSHOT-EVENT-001",
        "process_id": "SNAPSHOT-PROCESS-001",
        "actor_id": "AUTHORITY-001",
        "event_type": "ACTIVATE",
        "jurisdiction": "UG-DEMO",
        "rule_set": "KIN-SYS-DEMO-RULES",
        "rule_version": "1.0",
        "evidence": {
            "consignment_id": "SNAPSHOT-PROCESS-001",
            "commodity": "SYNTHETIC_MAIZE",
            "origin": "UGANDA-DEMO",
            "destination": "BORDER-DEMO",
            "rule_refs": ["SYNTHETIC-RULE-1"],
        },
    }


class RecoverySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.pipeline = KinSysProtocolPipeline(
            cee_database=str(root / "source-cee.sqlite3"),
            ivm_database=str(root / "source-ivm.sqlite3"),
        )
        self.pipeline.execute(request())

    def tearDown(self):
        self.pipeline.close()
        self.temp.cleanup()

    def test_snapshot_pair_has_hashes_provenance_and_verifies(self):
        destination = Path(self.temp.name) / "snapshot-set"
        result = create_recovery_snapshot_set(
            self.pipeline, destination, "SNAPSHOT-OP-001"
        )
        self.assertEqual(result["status"], "SNAPSHOT_SET_CREATED")
        manifest = result["manifest"]
        self.assertEqual(manifest["pair_atomicity"], "NOT_ATOMIC_ACROSS_CEE_AND_IVM")
        self.assertEqual(manifest["pair_consistency"], "NOT_EVALUATED_BY_SNAPSHOT_CREATION")
        self.assertEqual(manifest["stores"]["cee"]["source"]["kind"], "FILE")
        self.assertEqual(manifest["stores"]["ivm"]["source"]["kind"], "FILE")
        for role in ("cee", "ivm"):
            snapshot = manifest["stores"][role]["snapshot"]
            self.assertEqual(len(snapshot["sha256"]), 64)
            self.assertEqual(snapshot["integrity"], "OK")
            self.assertTrue((destination / snapshot["filename"]).exists())
            self.assertFalse((destination / snapshot["filename"]).stat().st_mode & 0o222)
        verified = verify_recovery_snapshot_set(destination)
        self.assertEqual(verified["status"], "VERIFIED")
        self.assertEqual(verified["stores"]["cee"]["row_counts"]["cee_operations"], 1)
        self.assertEqual(verified["stores"]["ivm"]["row_counts"]["events"], 1)

    def test_snapshots_are_read_only_openable_and_source_is_unchanged(self):
        destination = Path(self.temp.name) / "snapshot-set"
        before = {
            "cee": self.pipeline.cee.db.execute("SELECT COUNT(*) FROM cee_events").fetchone()[0],
            "ivm": self.pipeline.ivm.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        }
        result = create_recovery_snapshot_set(self.pipeline, destination, "SNAPSHOT-OP-001")
        after = {
            "cee": self.pipeline.cee.db.execute("SELECT COUNT(*) FROM cee_events").fetchone()[0],
            "ivm": self.pipeline.ivm.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        }
        self.assertEqual(before, after)
        snapshot = destination / result["manifest"]["stores"]["cee"]["snapshot"]["filename"]
        connection = sqlite3.connect("file:" + str(snapshot.resolve()) + "?mode=ro", uri=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM cee_events")
        finally:
            connection.close()

    def test_modified_snapshot_file_fails_digest_verification(self):
        destination = Path(self.temp.name) / "snapshot-set"
        result = create_recovery_snapshot_set(self.pipeline, destination, "SNAPSHOT-OP-001")
        path = destination / result["manifest"]["stores"]["ivm"]["snapshot"]["filename"]
        path.chmod(0o644)
        with path.open("ab") as handle:
            handle.write(b"tamper")
        verified = verify_recovery_snapshot_set(destination)
        self.assertEqual(verified["status"], "FAILED")
        self.assertEqual(verified["stores"]["ivm"]["status"], "FILE_DIGEST_MISMATCH")

    def test_manifest_modification_fails_verification(self):
        destination = Path(self.temp.name) / "snapshot-set"
        create_recovery_snapshot_set(self.pipeline, destination, "SNAPSHOT-OP-001")
        manifest_path = destination / "manifest.json"
        manifest_path.chmod(0o644)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))\n        manifest["operation_id"] = "ALTERED-OPERATION"\n        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        self.assertEqual(
            verify_recovery_snapshot_set(destination)["status"],
            "MANIFEST_DIGEST_MISMATCH",
        )

    def test_existing_destination_is_never_overwritten(self):
        destination = Path(self.temp.name) / "snapshot-set"
        destination.mkdir()
        marker = destination / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            create_recovery_snapshot_set(self.pipeline, destination, "SNAPSHOT-OP-001")
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
