#!/usr/bin/env python3
"""Socket-based Two-Phase Commit simulator with durable write-ahead logs."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


class WALCorruptionError(ValueError):
    """Raised when a durable WAL record cannot be decoded safely."""


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
