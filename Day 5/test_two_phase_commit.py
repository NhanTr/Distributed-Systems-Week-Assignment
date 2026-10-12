from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from two_phase_commit import (
    Coordinator,
    JsonLineServer,
    Participant,
    ProtocolError,
    TraceLog,
    TransactionResult,
    WALCorruptionError,
    WriteAheadLog,
    rpc,
)


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


class JsonLineServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        wal = WriteAheadLog(
            Path(self.temporary_directory.name) / "P1.wal", "P1"
        )
        self.participant = Participant("P1", wal)

    def start_server(self, handler=None) -> JsonLineServer:
        server = JsonLineServer(handler or self.participant.handle)
        server.start()
        self.addCleanup(server.stop)
        return server

    @staticmethod
    def raw_request(address: tuple[str, int], payload: bytes) -> dict[str, object]:
        with socket.create_connection(address, timeout=1.0) as connection:
            connection.settimeout(1.0)
            connection.sendall(payload)
            data = bytearray()
            while not data.endswith(b"\n"):
                chunk = connection.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
        response = json.loads(data.decode("utf-8"))
        if not isinstance(response, dict):
            raise AssertionError("server response was not a JSON object")
        return response

    def test_rpc_round_trip_to_participant_server(self) -> None:
        server = self.start_server()

        response = rpc(
            server.address,
            {"type": "VOTE_REQUEST", "tx_id": "101"},
        )

        self.assertEqual(response, {"type": "VOTE_COMMIT", "tx_id": "101"})

    def test_malformed_json_returns_error_and_server_stays_available(self) -> None:
        server = self.start_server()

        malformed_response = self.raw_request(server.address, b"{not-json}\n")
        valid_response = rpc(
            server.address,
            {"type": "VOTE_REQUEST", "tx_id": "101"},
        )

        self.assertEqual(malformed_response["type"], "ERROR")
        self.assertEqual(valid_response["type"], "VOTE_COMMIT")

    def test_oversized_message_returns_error(self) -> None:
        calls: list[dict[str, object]] = []

        def recording_handler(message: dict[str, object]) -> dict[str, object]:
            calls.append(message)
            return {"type": "ACK", "tx_id": "unused"}

        server = self.start_server(recording_handler)

        response = self.raw_request(server.address, b"x" * 65_537 + b"\n")

        self.assertEqual(response["type"], "ERROR")
        self.assertEqual(calls, [])

    def test_rpc_rejects_response_without_json_object(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)

        def return_json_list() -> None:
            connection, _ = listener.accept()
            with connection:
                connection.recv(4096)
                connection.sendall(b'["not", "an", "object"]\n')

        thread = threading.Thread(target=return_json_list, daemon=True)
        thread.start()

        with self.assertRaises(ProtocolError):
            rpc(
                (str(listener.getsockname()[0]), int(listener.getsockname()[1])),
                {"type": "VOTE_REQUEST", "tx_id": "101"},
            )
        thread.join(timeout=1.0)


class CoordinatorIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.trace = TraceLog()
        self.participants: dict[str, Participant] = {}
        self.servers: dict[str, JsonLineServer] = {}
        for node_id in ("P1", "P2", "P3"):
            participant = Participant(
                node_id,
                WriteAheadLog(self.directory / f"{node_id}.wal", node_id),
                abort_transactions={"102"} if node_id == "P3" else None,
                trace=self.trace,
            )
            server = JsonLineServer(participant.handle)
            server.start()
            self.addCleanup(server.stop)
            self.participants[node_id] = participant
            self.servers[node_id] = server

    def coordinator(
        self,
        addresses: dict[str, tuple[str, int]] | None = None,
        timeout: float = 0.2,
        wal_name: str = "coordinator.wal",
    ) -> Coordinator:
        return Coordinator(
            addresses or {
                node_id: server.address for node_id, server in self.servers.items()
            },
            WriteAheadLog(self.directory / wal_name, "COORD"),
            trace=self.trace,
            timeout=timeout,
        )

    def test_all_commit_votes_produce_global_commit(self) -> None:
        coordinator = self.coordinator()

        result = coordinator.execute("101")

        self.assertIsInstance(result, TransactionResult)
        self.assertEqual(result.decision, "GLOBAL_COMMIT")
        self.assertEqual(result.votes, {"P1": "VOTE_COMMIT", "P2": "VOTE_COMMIT", "P3": "VOTE_COMMIT"})
        self.assertEqual(result.acknowledgements, {"P1": "ACK", "P2": "ACK", "P3": "ACK"})
        self.assertEqual(
            {node_id: participant.states["101"] for node_id, participant in self.participants.items()},
            {"P1": "COMMIT", "P2": "COMMIT", "P3": "COMMIT"},
        )
        self.assertEqual(
            [record["state"] for record in coordinator.wal.records()],
            ["START_2PC", "GLOBAL_COMMIT"],
        )

    def test_one_abort_vote_forces_global_abort(self) -> None:
        coordinator = self.coordinator()

        result = coordinator.execute("102")

        self.assertEqual(result.decision, "GLOBAL_ABORT")
        self.assertEqual(result.votes["P3"], "VOTE_ABORT")
        self.assertEqual(
            {node_id: participant.states["102"] for node_id, participant in self.participants.items()},
            {"P1": "ABORT", "P2": "ABORT", "P3": "ABORT"},
        )

    def test_unavailable_participant_forces_abort_and_records_missing_ack(self) -> None:
        unused = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        unused.bind(("127.0.0.1", 0))
        unavailable_address = ("127.0.0.1", int(unused.getsockname()[1]))
        unused.close()
        addresses = {
            "P1": self.servers["P1"].address,
            "P2": self.servers["P2"].address,
            "P3": unavailable_address,
        }
        coordinator = self.coordinator(addresses, wal_name="unavailable.wal")

        result = coordinator.execute("201")

        self.assertEqual(result.decision, "GLOBAL_ABORT")
        self.assertEqual(result.votes["P3"], "UNAVAILABLE")
        self.assertEqual(result.acknowledgements["P3"], "UNAVAILABLE")

    def test_nonresponsive_participant_times_out_and_forces_abort(self) -> None:
        def slow_handler(message: dict[str, object]) -> dict[str, object]:
            time.sleep(0.25)
            return {"type": "VOTE_COMMIT", "tx_id": message.get("tx_id")}

        slow_server = JsonLineServer(slow_handler)
        slow_server.start()
        self.addCleanup(slow_server.stop)
        addresses = {
            "P1": self.servers["P1"].address,
            "P2": self.servers["P2"].address,
            "P3": slow_server.address,
        }
        coordinator = self.coordinator(
            addresses, timeout=0.05, wal_name="timeout.wal"
        )

        started = time.monotonic()
        result = coordinator.execute("202")
        elapsed = time.monotonic() - started

        self.assertEqual(result.decision, "GLOBAL_ABORT")
        self.assertEqual(result.votes["P3"], "UNAVAILABLE")
        self.assertLess(elapsed, 0.3)

    def test_invalid_vote_forces_abort(self) -> None:
        def invalid_vote_handler(message: dict[str, object]) -> dict[str, object]:
            if message.get("type") == "VOTE_REQUEST":
                return {"type": "NOT_A_VOTE", "tx_id": message.get("tx_id")}
            return {"type": "ACK", "tx_id": message.get("tx_id")}

        invalid_server = JsonLineServer(invalid_vote_handler)
        invalid_server.start()
        self.addCleanup(invalid_server.stop)
        addresses = {
            "P1": self.servers["P1"].address,
            "P2": self.servers["P2"].address,
            "P3": invalid_server.address,
        }
        coordinator = self.coordinator(addresses, wal_name="invalid.wal")

        result = coordinator.execute("203")

        self.assertEqual(result.decision, "GLOBAL_ABORT")
        self.assertEqual(result.votes["P3"], "INVALID")

    def test_repeated_transaction_returns_durable_decision_without_new_round(self) -> None:
        coordinator = self.coordinator()

        first = coordinator.execute("101")
        second = coordinator.execute("101")

        self.assertEqual(second.decision, first.decision)
        self.assertEqual(
            [record["state"] for record in coordinator.wal.records()].count("START_2PC"),
            1,
        )

    def test_coordinator_handler_rejects_invalid_execute(self) -> None:
        coordinator = self.coordinator()
        messages = [
            {"type": "VOTE_REQUEST", "tx_id": "101"},
            {"type": "EXECUTE"},
            {"type": "EXECUTE", "tx_id": ""},
            {"type": "EXECUTE", "tx_id": 101},
        ]

        for message in messages:
            with self.subTest(message=message):
                self.assertEqual(coordinator.handle(message)["type"], "ERROR")


if __name__ == "__main__":
    unittest.main()
