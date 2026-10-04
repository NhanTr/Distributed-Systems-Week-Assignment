#!/usr/bin/env python3
"""Five-node Bully election simulator using threads and TCP sockets."""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from dataclasses import dataclass


Address = tuple[str, int]


class TraceLog:
    def __init__(self) -> None:
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def emit(self, actor: str, event: str) -> None:
        with self._lock:
            self._lines.append(f"[{actor}] {event}")

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._lines)


@dataclass(frozen=True)
class SimulationResult:
    leader_views: dict[int, int | None]
    trace: list[str]


class ProcessNode(threading.Thread):
    def __init__(
        self,
        process_id: int,
        listener: socket.socket,
        addresses: dict[int, Address],
        trace: TraceLog,
        initial_leader: int,
        failure_timeout: float,
        rpc_timeout: float,
        heartbeat_interval: float,
        coordinator_timeout: float,
    ) -> None:
        super().__init__(name=f"P{process_id}")
        self.process_id = process_id
        self.listener = listener
        self.addresses = addresses
        self.trace = trace
        self.failure_timeout = failure_timeout
        self.rpc_timeout = rpc_timeout
        self.heartbeat_interval = heartbeat_interval
        self.coordinator_timeout = coordinator_timeout
        self.stop_event = threading.Event()
        self.coordinator_event = threading.Event()
        self.state_lock = threading.Lock()
        self.election_lock = threading.Lock()
        self.election_running = False
        self.leader_id: int | None = initial_leader
        self.last_heartbeat = time.monotonic()
        self.last_heartbeat_sent = 0.0

    def run(self) -> None:
        while not self.stop_event.is_set():
            self._serve_once()
            self._send_heartbeat_if_leader()
            self._detect_leader_failure()

    def stop(self) -> None:
        self.stop_event.set()
        try:
            self.listener.close()
        except OSError:
            pass

    def _serve_once(self) -> None:
        try:
            connection, _ = self.listener.accept()
        except socket.timeout:
            return
        except OSError:
            return

        with connection:
            connection.settimeout(self.rpc_timeout)
            payload = self._receive_json(connection)
            if payload is None:
                return
            message_type = payload.get("type")
            sender = int(payload.get("sender", 0))

            if message_type == "HEARTBEAT":
                self._record_coordinator(sender)
            elif message_type == "COORDINATOR":
                self._record_coordinator(sender)
                self.trace.emit(
                    f"P{self.process_id}",
                    f"Recv COORDINATOR <- P{sender}; leader is P{sender}",
                )
            elif message_type == "ELECTION" and sender < self.process_id:
                self.trace.emit(
                    f"P{self.process_id}",
                    f"Recv ELECTION <- P{sender}; reply OK",
                )
                self._send_json(connection, {"type": "OK", "sender": self.process_id})
                self._start_election("higher-priority reply")

    def _receive_json(self, connection: socket.socket) -> dict[str, object] | None:
        data = bytearray()
        try:
            while not data.endswith(b"\n"):
                chunk = connection.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
        except (OSError, socket.timeout):
            return None
        if not data:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _send_json(connection: socket.socket, payload: dict[str, object]) -> None:
        connection.sendall(json.dumps(payload).encode("utf-8") + b"\n")

    def _rpc(
        self, target: int, message_type: str, expect_response: bool
    ) -> dict[str, object] | None:
        try:
            with socket.create_connection(
                self.addresses[target], timeout=self.rpc_timeout
            ) as connection:
                connection.settimeout(self.rpc_timeout)
                self._send_json(
                    connection, {"type": message_type, "sender": self.process_id}
                )
                if not expect_response:
                    return {}
                return self._receive_json(connection)
        except (OSError, socket.timeout):
            return None

    def _record_coordinator(self, leader_id: int) -> None:
        with self.state_lock:
            self.leader_id = leader_id
            self.last_heartbeat = time.monotonic()
        self.coordinator_event.set()

    def _send_heartbeat_if_leader(self) -> None:
        with self.state_lock:
            is_leader = self.leader_id == self.process_id
        now = time.monotonic()
        if not is_leader or now - self.last_heartbeat_sent < self.heartbeat_interval:
            return
        self.last_heartbeat_sent = now
        for target in self.addresses:
            if target != self.process_id:
                self._rpc(target, "HEARTBEAT", expect_response=False)

    def _detect_leader_failure(self) -> None:
        with self.state_lock:
            leader_id = self.leader_id
            heartbeat_age = time.monotonic() - self.last_heartbeat
        if leader_id == self.process_id or heartbeat_age < self.failure_timeout:
            return
        self._start_election("leader heartbeat timeout")

    def _start_election(self, reason: str) -> None:
        with self.election_lock:
            if self.election_running or self.stop_event.is_set():
                return
            self.election_running = True
        if reason == "leader heartbeat timeout":
            self.trace.emit(
                f"P{self.process_id}", "Timeout detected! Initiating Election..."
            )
        self.coordinator_event.clear()
        threading.Thread(
            target=self._run_election,
            name=f"P{self.process_id}-election",
            daemon=True,
        ).start()

    def _run_election(self) -> None:
        try:
            higher_processes = [
                process_id
                for process_id in sorted(self.addresses)
                if process_id > self.process_id
            ]
            if higher_processes:
                targets = ", ".join(f"P{process_id}" for process_id in higher_processes)
                self.trace.emit(
                    f"P{self.process_id}", f"Send ELECTION -> {targets}"
                )

            responders: list[int] = []
            for target in higher_processes:
                response = self._rpc(target, "ELECTION", expect_response=True)
                if response and response.get("type") == "OK":
                    responders.append(target)
                    self.trace.emit(f"P{self.process_id}", f"Recv OK <- P{target}")
                else:
                    self.trace.emit(
                        f"P{self.process_id}", f"Timeout/no response from P{target}"
                    )

            if not responders:
                self._become_leader()
                return

            if not self.coordinator_event.wait(self.coordinator_timeout):
                self.trace.emit(
                    f"P{self.process_id}",
                    "Coordinator announcement timed out; restarting election",
                )
                with self.election_lock:
                    self.election_running = False
                self._start_election("coordinator timeout")
                return
        finally:
            with self.election_lock:
                self.election_running = False

    def _become_leader(self) -> None:
        self._record_coordinator(self.process_id)
        self.trace.emit(
            f"P{self.process_id}", "No higher process replied -> NEW LEADER!"
        )
        recipients = [
            process_id
            for process_id in sorted(self.addresses)
            if process_id < self.process_id
        ]
        targets = ", ".join(f"P{process_id}" for process_id in recipients)
        self.trace.emit(
            f"P{self.process_id}", f"Broadcast COORDINATOR victory to {targets}"
        )
        for target in recipients:
            self._rpc(target, "COORDINATOR", expect_response=False)


class BullyCluster:
    def __init__(
        self,
        rpc_timeout: float = 0.12,
        heartbeat_interval: float = 0.08,
        coordinator_timeout: float = 0.8,
    ) -> None:
        self.trace = TraceLog()
        self.listeners = self._create_listeners(5)
        self.addresses = {
            process_id: listener.getsockname()
            for process_id, listener in self.listeners.items()
        }
        failure_timeouts = {1: 0.75, 2: 0.30, 3: 0.55, 4: 0.65, 5: 1.0}
        self.nodes = {
            process_id: ProcessNode(
                process_id=process_id,
                listener=self.listeners[process_id],
                addresses=self.addresses,
                trace=self.trace,
                initial_leader=5,
                failure_timeout=failure_timeouts[process_id],
                rpc_timeout=rpc_timeout,
                heartbeat_interval=heartbeat_interval,
                coordinator_timeout=coordinator_timeout,
            )
            for process_id in self.listeners
        }

    @staticmethod
    def _create_listeners(count: int) -> dict[int, socket.socket]:
        listeners: dict[int, socket.socket] = {}
        for process_id in range(1, count + 1):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(0.03)
            listeners[process_id] = listener
        return listeners

    def run_failure_demo(self, timeout: float = 4.0) -> SimulationResult:
        for node in self.nodes.values():
            node.start()
        time.sleep(0.25)

        self.trace.emit("SYSTEM", "Active Leader P5 terminated.")
        self.nodes[5].stop()
        self.nodes[5].join(timeout=1.0)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            leader_views = {
                process_id: node.leader_id
                for process_id, node in self.nodes.items()
                if process_id != 5
            }
            if leader_views and set(leader_views.values()) == {4}:
                self.trace.emit(
                    "SYSTEM", "Verification passed: P1-P4 agree that P4 is leader."
                )
                break
            time.sleep(0.02)
        else:
            raise TimeoutError("P1-P4 did not converge on P4 before the deadline")

        result = SimulationResult(
            leader_views={
                process_id: node.leader_id
                for process_id, node in self.nodes.items()
                if process_id != 5
            },
            trace=self.trace.snapshot(),
        )
        self.stop()
        return result

    def stop(self) -> None:
        for node in self.nodes.values():
            node.stop()
        for node in self.nodes.values():
            if node.is_alive():
                node.join(timeout=1.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        help="also write the console trace to this UTF-8 text file",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    cluster = BullyCluster()
    try:
        result = cluster.run_failure_demo()
    finally:
        cluster.stop()
    output = "\n".join(result.trace) + "\n"
    print(output, end="")
    if arguments.output:
        with open(arguments.output, "w", encoding="utf-8") as stream:
            stream.write(output)


if __name__ == "__main__":
    main()
