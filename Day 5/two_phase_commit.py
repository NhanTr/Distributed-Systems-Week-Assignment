#!/usr/bin/env python3
"""Socket-based Two-Phase Commit simulator with durable write-ahead logs."""

from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class WALCorruptionError(ValueError):
    """Raised when a durable WAL record cannot be decoded safely."""


class ProtocolError(ValueError):
    """Raised when a peer violates the JSON-line protocol."""


class TraceLog:
    """Thread-safe, human-readable event collector."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def emit(self, actor: str, event: str) -> None:
        with self._lock:
            self._lines.append(f"[{actor}] {event}")

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._lines)


class WriteAheadLog:
    """Append-only JSON-lines log whose writes reach stable storage."""

    REQUIRED_FIELDS = {"timestamp", "node_id", "tx_id", "state", "details"}

    def __init__(self, path: Path, node_id: str) -> None:
        self.path = Path(path)
        self.node_id = node_id
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        tx_id: str,
        state: str,
        details: dict[str, object] | None = None,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "node_id": self.node_id,
            "tx_id": tx_id,
            "state": state,
            "details": details or {},
        }
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())

    def records(self) -> list[dict[str, object]]:
        with self._lock:
            if not self.path.exists():
                return []
            content = self.path.read_text(encoding="utf-8")

        lines = content.splitlines()
        records: list[dict[str, object]] = []
        for index, line in enumerate(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                is_torn_final_line = index == len(lines) - 1 and not content.endswith("\n")
                if is_torn_final_line:
                    break
                raise WALCorruptionError(
                    f"Invalid WAL record at line {index + 1}: {error.msg}"
                ) from error
            if not isinstance(record, dict) or not self.REQUIRED_FIELDS.issubset(record):
                raise WALCorruptionError(f"Invalid WAL record at line {index + 1}")
            records.append(record)
        return records

    def recover_states(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for record in self.records():
            states[str(record["tx_id"])] = str(record["state"])
        return states


class Participant:
    """A durable participant state machine for one branch of a 2PC transaction."""

    RECOVERED_STATES = {
        "VOTE_COMMIT": "READY",
        "VOTE_ABORT": "ABORT",
        "GLOBAL_COMMIT": "COMMIT",
        "GLOBAL_ABORT": "ABORT",
    }

    def __init__(
        self,
        node_id: str,
        wal: WriteAheadLog,
        abort_transactions: set[str] | None = None,
        trace: TraceLog | None = None,
    ) -> None:
        self.node_id = node_id
        self.wal = wal
        self.abort_transactions = set(abort_transactions or set())
        self.trace = trace or TraceLog()
        self._lock = threading.RLock()
        self._raw_states = self.wal.recover_states()
        self._states = {
            tx_id: self.RECOVERED_STATES.get(state, state)
            for tx_id, state in self._raw_states.items()
        }

    @property
    def states(self) -> dict[str, str]:
        with self._lock:
            return dict(self._states)

    @staticmethod
    def _error(tx_id: object, reason: str) -> dict[str, object]:
        return {"type": "ERROR", "tx_id": tx_id, "error": reason}

    def handle(self, message: dict[str, object]) -> dict[str, object]:
        message_type = message.get("type")
        tx_id = message.get("tx_id")
        if not isinstance(tx_id, str) or not tx_id.strip():
            return self._error(tx_id, "tx_id must be a non-empty string")
        if message_type not in {"VOTE_REQUEST", "GLOBAL_COMMIT", "GLOBAL_ABORT"}:
            return self._error(tx_id, f"unsupported message type: {message_type}")

        with self._lock:
            if message_type == "VOTE_REQUEST":
                return self._handle_vote_request(tx_id)
            return self._handle_decision(tx_id, str(message_type))

    def _handle_vote_request(self, tx_id: str) -> dict[str, object]:
        current = self._states.get(tx_id)
        if current in {"READY", "COMMIT"}:
            return {"type": "VOTE_COMMIT", "tx_id": tx_id}
        if current == "ABORT":
            return {"type": "VOTE_ABORT", "tx_id": tx_id}

        if tx_id in self.abort_transactions:
            self.wal.append(tx_id, "VOTE_ABORT", {"reason": "lock conflict"})
            self._raw_states[tx_id] = "VOTE_ABORT"
            self._states[tx_id] = "ABORT"
            self.trace.emit(
                self.node_id,
                "Lock Conflict! -> Log VOTE_ABORT -> Reply VOTE_ABORT",
            )
            return {"type": "VOTE_ABORT", "tx_id": tx_id}

        self.wal.append(tx_id, "VOTE_COMMIT")
        self._raw_states[tx_id] = "VOTE_COMMIT"
        self._states[tx_id] = "READY"
        self.trace.emit(
            self.node_id,
            "Log VOTE_COMMIT -> Reply VOTE_COMMIT",
        )
        return {"type": "VOTE_COMMIT", "tx_id": tx_id}

    def _handle_decision(self, tx_id: str, message_type: str) -> dict[str, object]:
        current = self._states.get(tx_id)
        target = "COMMIT" if message_type == "GLOBAL_COMMIT" else "ABORT"
        durable_state = message_type

        if current == target and self._raw_states.get(tx_id) == durable_state:
            return {"type": "ACK", "tx_id": tx_id}
        if current in {"COMMIT", "ABORT"} and current != target:
            return self._error(
                tx_id,
                f"conflicting decision: transaction is already {current}",
            )
        if current is None:
            return self._error(tx_id, "decision received before vote request")
        if message_type == "GLOBAL_COMMIT" and current != "READY":
            return self._error(tx_id, "cannot commit a transaction that did not vote commit")

        self.wal.append(tx_id, durable_state)
        self._raw_states[tx_id] = durable_state
        self._states[tx_id] = target
        action = "Apply" if target == "COMMIT" else "Rollback"
        self.trace.emit(
            self.node_id,
            f"Recv {message_type} -> {action} -> Reply ACK",
        )
        return {"type": "ACK", "tx_id": tx_id}


def _encode_message(message: dict[str, object]) -> bytes:
    return json.dumps(message, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"


def _receive_line(connection: socket.socket, max_message_bytes: int) -> bytes:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = connection.recv(min(4096, max_message_bytes + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_message_bytes:
            raise ProtocolError(f"message exceeds {max_message_bytes} bytes")
    if not data:
        raise ProtocolError("peer closed the connection without a message")
    if not data.endswith(b"\n"):
        raise ProtocolError("message is missing newline terminator")
    return bytes(data[:-1])


def _decode_object(payload: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError(f"invalid JSON message: {error}") from error
    if not isinstance(decoded, dict):
        raise ProtocolError("JSON message must be an object")
    return decoded


def rpc(
    address: tuple[str, int],
    message: dict[str, object],
    timeout: float = 1.0,
) -> dict[str, object]:
    """Send one request and receive one object response over TCP."""

    with socket.create_connection(address, timeout=timeout) as connection:
        connection.settimeout(timeout)
        connection.sendall(_encode_message(message))
        response = _receive_line(connection, 65_536)
    return _decode_object(response)


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _JsonLineRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        owner: JsonLineServer = self.server.owner  # type: ignore[attr-defined]
        self.request.settimeout(owner.timeout)
        try:
            payload = _receive_line(self.request, owner.max_message_bytes)
            message = _decode_object(payload)
            response = owner.handler(message)
            if not isinstance(response, dict):
                raise ProtocolError("handler response must be a JSON object")
        except (OSError, ProtocolError, ValueError) as error:
            response = {"type": "ERROR", "tx_id": None, "error": str(error)}
        try:
            self.request.sendall(_encode_message(response))
        except OSError:
            return


class JsonLineServer:
    """Lifecycle wrapper for a threaded, one-request-per-connection JSON server."""

    def __init__(
        self,
        handler: Callable[[dict[str, object]], dict[str, object]],
        address: tuple[str, int] = ("127.0.0.1", 0),
        max_message_bytes: int = 65_536,
        timeout: float = 1.0,
    ) -> None:
        self.handler = handler
        self.max_message_bytes = max_message_bytes
        self.timeout = timeout
        self._server = _ThreadingTCPServer(address, _JsonLineRequestHandler)
        self._server.owner = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None
        self._stopped = False

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address
        return str(host), int(port)

    def start(self) -> None:
        if self._thread is not None:
            return
        if self._stopped:
            raise RuntimeError("a stopped server cannot be restarted")
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"JsonLineServer-{self.address[1]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=2.0)
        self._server.server_close()


@dataclass(frozen=True)
class TransactionResult:
    tx_id: str
    decision: str
    votes: dict[str, str]
    acknowledgements: dict[str, str]


class Coordinator:
    """Durable coordinator for the voting and decision phases of 2PC."""

    def __init__(
        self,
        participants: dict[str, tuple[str, int]],
        wal: WriteAheadLog,
        trace: TraceLog | None = None,
        timeout: float = 1.0,
    ) -> None:
        self.participants = dict(participants)
        self.wal = wal
        self.trace = trace or TraceLog()
        self.timeout = timeout
        self._lock = threading.RLock()
        self._decisions = {
            tx_id: state
            for tx_id, state in self.wal.recover_states().items()
            if state in {"GLOBAL_COMMIT", "GLOBAL_ABORT"}
        }

    @staticmethod
    def _error(tx_id: object, reason: str) -> dict[str, object]:
        return {"type": "ERROR", "tx_id": tx_id, "error": reason}

    def handle(self, message: dict[str, object]) -> dict[str, object]:
        tx_id = message.get("tx_id")
        if message.get("type") != "EXECUTE":
            return self._error(tx_id, "coordinator accepts only EXECUTE")
        if not isinstance(tx_id, str) or not tx_id.strip():
            return self._error(tx_id, "tx_id must be a non-empty string")
        result = self.execute(tx_id)
        return {"type": "RESULT", **asdict(result)}

    def execute(self, tx_id: str) -> TransactionResult:
        if not isinstance(tx_id, str) or not tx_id.strip():
            raise ValueError("tx_id must be a non-empty string")

        with self._lock:
            prior_decision = self._decisions.get(tx_id)
            if prior_decision is not None:
                return TransactionResult(tx_id, prior_decision, {}, {})

            self.wal.append(tx_id, "START_2PC")
            self.trace.emit("COORD", f"Tx {tx_id} Started -> Log START_2PC")
            self.trace.emit("COORD", "Multicast VOTE_REQUEST")
            votes = self._multicast(
                {"type": "VOTE_REQUEST", "tx_id": tx_id},
                {"VOTE_COMMIT", "VOTE_ABORT"},
            )

            if votes and all(vote == "VOTE_COMMIT" for vote in votes.values()):
                decision = "GLOBAL_COMMIT"
                self.trace.emit(
                    "COORD", "Recv all VOTE_COMMIT -> Log GLOBAL_COMMIT"
                )
            else:
                decision = "GLOBAL_ABORT"
                self.trace.emit("COORD", "Recv VOTE_ABORT -> Log GLOBAL_ABORT")

            self.wal.append(tx_id, decision, {"votes": votes})
            self._decisions[tx_id] = decision
            self.trace.emit("COORD", f"Multicast {decision}")
            acknowledgements = self._multicast(
                {"type": decision, "tx_id": tx_id},
                {"ACK"},
            )
            outcome = "Committed" if decision == "GLOBAL_COMMIT" else "Aborted"
            self.trace.emit("SYSTEM", f"Tx {tx_id} {outcome} Cleanly.")
            return TransactionResult(tx_id, decision, votes, acknowledgements)

    def _multicast(
        self,
        message: dict[str, object],
        valid_types: set[str],
    ) -> dict[str, str]:
        if not self.participants:
            return {}
        with ThreadPoolExecutor(max_workers=len(self.participants)) as executor:
            futures = {
                node_id: executor.submit(
                    self._request,
                    address,
                    message,
                    valid_types,
                )
                for node_id, address in self.participants.items()
            }
            return {
                node_id: futures[node_id].result()
                for node_id in sorted(futures)
            }

    def _request(
        self,
        address: tuple[str, int],
        message: dict[str, object],
        valid_types: set[str],
    ) -> str:
        try:
            response = rpc(address, message, timeout=self.timeout)
        except (OSError, TimeoutError):
            return "UNAVAILABLE"
        except ProtocolError:
            return "INVALID"
        response_type = response.get("type")
        if isinstance(response_type, str) and response_type in valid_types:
            return response_type
        return "INVALID"
