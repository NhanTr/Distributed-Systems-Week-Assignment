from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from two_phase_commit import Participant, TraceLog, WALCorruptionError, WriteAheadLog


class WriteAheadLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "node.wal"
        self.wal = WriteAheadLog(self.path, "P1")

    def test_append_fsyncs_and_recovers_last_state(self) -> None:
        with patch("two_phase_commit.os.fsync") as fsync:
            self.wal.append("101", "VOTE_COMMIT")
            self.wal.append("101", "GLOBAL_COMMIT", {"source": "coordinator"})

        self.assertEqual(fsync.call_count, 2)
        self.assertEqual(self.wal.recover_states(), {"101": "GLOBAL_COMMIT"})
        self.assertEqual(
            [record["state"] for record in self.wal.records()],
            ["VOTE_COMMIT", "GLOBAL_COMMIT"],
        )
        self.assertEqual(self.wal.records()[1]["details"], {"source": "coordinator"})

    def test_recovery_ignores_truncated_final_line(self) -> None:
        valid_record = {
            "timestamp": "2026-10-12T00:00:00+00:00",
            "node_id": "P1",
            "tx_id": "101",
            "state": "VOTE_COMMIT",
            "details": {},
        }
        self.path.write_text(
            json.dumps(valid_record) + "\n" + '{"tx_id":"102"', encoding="utf-8"
        )

        self.assertEqual(self.wal.recover_states(), {"101": "VOTE_COMMIT"})
        self.assertEqual(len(self.wal.records()), 1)

    def test_recovery_rejects_corruption_before_final_line(self) -> None:
        valid_record = {
            "timestamp": "2026-10-12T00:00:00+00:00",
            "node_id": "P1",
            "tx_id": "101",
            "state": "VOTE_COMMIT",
            "details": {},
        }
        self.path.write_text(
            "not-json\n" + json.dumps(valid_record) + "\n", encoding="utf-8"
        )

        with self.assertRaises(WALCorruptionError):
            self.wal.records()


class ParticipantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "P1.wal"
        self.trace = TraceLog()
        self.wal = WriteAheadLog(self.path, "P1")

    def test_vote_commit_is_durable_before_reply(self) -> None:
        participant = Participant("P1", self.wal, trace=self.trace)

        response = participant.handle({"type": "VOTE_REQUEST", "tx_id": "101"})

        self.assertEqual(response, {"type": "VOTE_COMMIT", "tx_id": "101"})
        self.assertEqual(participant.states["101"], "READY")
        self.assertEqual(self.wal.recover_states()["101"], "VOTE_COMMIT")

    def test_injected_lock_conflict_votes_abort(self) -> None:
        participant = Participant(
            "P1", self.wal, abort_transactions={"102"}, trace=self.trace
        )

        response = participant.handle({"type": "VOTE_REQUEST", "tx_id": "102"})

        self.assertEqual(response, {"type": "VOTE_ABORT", "tx_id": "102"})
        self.assertEqual(participant.states["102"], "ABORT")
        self.assertIn(
            "[P1] Lock Conflict! -> Log VOTE_ABORT -> Reply VOTE_ABORT",
            self.trace.snapshot(),
        )

    def test_global_commit_and_abort_are_idempotent(self) -> None:
        participant = Participant(
            "P1", self.wal, abort_transactions={"202"}, trace=self.trace
        )
        participant.handle({"type": "VOTE_REQUEST", "tx_id": "201"})
        participant.handle({"type": "VOTE_REQUEST", "tx_id": "202"})

        first_commit = participant.handle({"type": "GLOBAL_COMMIT", "tx_id": "201"})
        second_commit = participant.handle({"type": "GLOBAL_COMMIT", "tx_id": "201"})
        first_abort = participant.handle({"type": "GLOBAL_ABORT", "tx_id": "202"})
        second_abort = participant.handle({"type": "GLOBAL_ABORT", "tx_id": "202"})

        self.assertEqual(first_commit, {"type": "ACK", "tx_id": "201"})
        self.assertEqual(second_commit, first_commit)
        self.assertEqual(first_abort, {"type": "ACK", "tx_id": "202"})
        self.assertEqual(second_abort, first_abort)
        self.assertEqual(len(self.wal.records()), 4)

    def test_conflicting_final_decision_returns_error(self) -> None:
        participant = Participant("P1", self.wal, trace=self.trace)
        participant.handle({"type": "VOTE_REQUEST", "tx_id": "101"})
        participant.handle({"type": "GLOBAL_COMMIT", "tx_id": "101"})

        response = participant.handle({"type": "GLOBAL_ABORT", "tx_id": "101"})

        self.assertEqual(response["type"], "ERROR")
        self.assertEqual(participant.states["101"], "COMMIT")
        self.assertEqual(len(self.wal.records()), 2)

    def test_unknown_message_or_invalid_tx_id_returns_error(self) -> None:
        participant = Participant("P1", self.wal, trace=self.trace)
        messages = [
            {"type": "UNKNOWN", "tx_id": "101"},
            {"type": "VOTE_REQUEST"},
            {"type": "VOTE_REQUEST", "tx_id": ""},
            {"type": "VOTE_REQUEST", "tx_id": 101},
        ]

        for message in messages:
            with self.subTest(message=message):
                self.assertEqual(participant.handle(message)["type"], "ERROR")
        self.assertEqual(self.wal.records(), [])

    def test_restart_recovers_ready_commit_and_abort(self) -> None:
        participant = Participant(
            "P1", self.wal, abort_transactions={"303"}, trace=self.trace
        )
        participant.handle({"type": "VOTE_REQUEST", "tx_id": "301"})
        participant.handle({"type": "VOTE_REQUEST", "tx_id": "302"})
        participant.handle({"type": "GLOBAL_COMMIT", "tx_id": "302"})
        participant.handle({"type": "VOTE_REQUEST", "tx_id": "303"})
        participant.handle({"type": "GLOBAL_ABORT", "tx_id": "303"})

        recovered = Participant("P1", WriteAheadLog(self.path, "P1"))

        self.assertEqual(
            recovered.states,
            {"301": "READY", "302": "COMMIT", "303": "ABORT"},
        )


if __name__ == "__main__":
    unittest.main()
