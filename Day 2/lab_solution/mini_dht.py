#!/usr/bin/env python3
"""Small Chord-style registry node for the Day 2 distributed-systems lab."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import socketserver
import threading
import time
from dataclasses import dataclass


BITS = 8
RING_SIZE = 1 << BITS
REPLICAS = 2


def hash_id(value: str) -> int:
    return int(hashlib.sha1(value.encode()).hexdigest(), 16) % RING_SIZE


def rpc(endpoint: tuple[str, int], request: dict, timeout: float = 1.0) -> dict:
    with socket.create_connection(endpoint, timeout=timeout) as sock:
        sock.sendall((json.dumps(request) + "\n").encode())
        reply = sock.makefile("r", encoding="utf-8").readline()
    if not reply:
        raise ConnectionError("peer closed without replying")
    return json.loads(reply)


def clockwise_between(start: int, value: int, end: int) -> bool:
    """Return whether value is in the open clockwise interval (start, end)."""
    if start < end:
        return start < value < end
    return value > start or value < end


@dataclass(frozen=True)
class Peer:
    node_id: int
    host: str
    port: int

    @property
    def endpoint(self) -> tuple[str, int]:
        return self.host, self.port

    def as_dict(self) -> dict:
        return {"id": self.node_id, "host": self.host, "port": self.port}


class DHTState:
    def __init__(self, name: str, host: str, port: int, seeds: list[tuple[str, int]]):
        self.name = name
        self.me = Peer(hash_id(name), host, port)
        self.seeds = seeds
        self.peers: dict[int, Peer] = {self.me.node_id: self.me}
        self.records: dict[str, dict] = {}
        self.lock = threading.RLock()

    def add_peer(self, data: dict) -> None:
        peer = Peer(int(data["id"]), data["host"], int(data["port"]))
        with self.lock:
            self.peers[peer.node_id] = peer

    def sorted_peers(self) -> list[Peer]:
        with self.lock:
            return sorted(self.peers.values(), key=lambda p: p.node_id)

    def merge_members(self, members: list[dict]) -> None:
        for member in members:
            self.add_peer(member)

    def successor(self, key: int, offset: int = 0) -> Peer:
        peers = self.sorted_peers()
        start = next((i for i, p in enumerate(peers) if p.node_id >= key), 0)
        return peers[(start + offset) % len(peers)]

    def finger_table(self) -> list[Peer]:
        fingers: list[Peer] = []
        for i in range(BITS):
            peer = self.successor((self.me.node_id + (1 << i)) % RING_SIZE)
            if peer.node_id != self.me.node_id and peer not in fingers:
                fingers.append(peer)
        return fingers 

    def next_hops(self, key: int) -> list[Peer]:
        owner = self.successor(key)
        candidates = [
            p for p in self.finger_table()
            if clockwise_between(self.me.node_id, p.node_id, key)
        ]
        candidates.sort(
            key=lambda p: (p.node_id - self.me.node_id) % RING_SIZE,
            reverse=True,
        )
        ordered = candidates + [owner]
        result: list[Peer] = []
        for peer in ordered:
            if peer.node_id != self.me.node_id and peer not in result:
                result.append(peer)
        return result

    def route(self, request: dict) -> dict:
        key = int(request["key"])
        owner = self.successor(key)
        if owner.node_id == self.me.node_id:
            return self.handle_owner(request)

        failures: list[int] = []
        for peer in self.next_hops(key):
            try:
                response = rpc(peer.endpoint, request)
                response.setdefault("path", []).insert(0, self.me.node_id)
                return response
            except (OSError, ValueError, json.JSONDecodeError):
                failures.append(peer.node_id)

        # The owner may be down. A clockwise replica can still answer GET.
        if request["action"] == "GET":
            for offset in range(1, min(REPLICAS + 1, len(self.peers))):
                replica = self.successor(key, offset)
                if replica.node_id == self.me.node_id:
                    if request["name"] in self.records:
                        return self.handle_owner(request)
                    continue
                try:
                    response = rpc(replica.endpoint, {**request, "action": "REPLICA_GET"})
                    response.setdefault("path", []).insert(0, self.me.node_id)
                    return response
                except (OSError, ValueError, json.JSONDecodeError):
                    failures.append(replica.node_id)
        return {"ok": False, "error": "no reachable route", "failed": failures}

    def handle_owner(self, request: dict) -> dict:
        action = request["action"]
        name = request["name"]
        if action == "PUT":
            record = {
                "name": name,
                "host": request["host"],
                "port": int(request["port"]),
                "updated": time.time(),
            }
            with self.lock:
                self.records[name] = record
            for offset in range(1, min(REPLICAS + 1, len(self.peers))):
                peer = self.successor(int(request["key"]), offset)
                if peer.node_id == self.me.node_id:
                    continue
                try:
                    rpc(peer.endpoint, {"action": "REPLICA_PUT", "record": record})
                except OSError:
                    pass
            return {"ok": True, "owner": self.me.node_id, "path": [self.me.node_id]}
        record = self.records.get(name)
        return {
            "ok": record is not None,
            "record": record,
            "owner": self.me.node_id,
            "path": [self.me.node_id],
            **({"error": "name not found"} if record is None else {}),
        }

    def join_network(self) -> None:
        for seed in self.seeds:
            try:
                response = rpc(seed, {"action": "JOIN", "peer": self.me.as_dict()})
                self.merge_members(response.get("members", []))
            except OSError:
                continue
        # Share the merged view so every node can build the same ring.
        members = [p.as_dict() for p in self.sorted_peers()]
        for peer in self.sorted_peers():
            if peer.node_id == self.me.node_id:
                continue
            try:
                rpc(peer.endpoint, {"action": "MEMBERS", "members": members})
            except OSError:
                pass

    def register_self(self) -> dict:
        return self.route({
            "action": "PUT",
            "key": hash_id(self.name),
            "name": self.name,
            "host": self.me.host,
            "port": self.me.port,
        })


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            request = json.loads(self.rfile.readline())
            response = self.dispatch(request)
        except Exception as exc:  # Keep one bad client from terminating the node.
            response = {"ok": False, "error": str(exc)}
        self.wfile.write((json.dumps(response) + "\n").encode())

    def dispatch(self, request: dict) -> dict:
        state: DHTState = self.server.state  # type: ignore[attr-defined]
        action = request.get("action")
        if action == "PING":
            return {"ok": True, "peer": state.me.as_dict()}
        if action == "JOIN":
            state.add_peer(request["peer"])
            return {"ok": True, "members": [p.as_dict() for p in state.sorted_peers()]}
        if action == "MEMBERS":
            state.merge_members(request["members"])
            return {"ok": True}
        if action in {"PUT", "GET"}:
            return state.route(request)
        if action == "REPLICA_PUT":
            record = request["record"]
            with state.lock:
                state.records[record["name"]] = record
            return {"ok": True}
        if action == "REPLICA_GET":
            record = state.records.get(request["name"])
            return {"ok": record is not None, "record": record, "path": [state.me.node_id]}
        if action == "MESSAGE":
            print(f"MESSAGE from {request.get('from', 'unknown')}: {request['text']}", flush=True)
            return {"ok": True, "delivered_to": state.name}
        if action == "STATUS":
            return {
                "ok": True,
                "me": state.me.as_dict(),
                "members": [p.as_dict() for p in state.sorted_peers()],
                "fingers": [p.as_dict() for p in state.finger_table()],
                "records": list(state.records.values()),
            }
        return {"ok": False, "error": f"unknown action: {action}"}


class ThreadedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def parse_endpoint(text: str) -> tuple[str, int]:
    host, port = text.rsplit(":", 1)
    return host, int(port)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="stable logical node name")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--seed", action="append", default=[], help="host:port; repeatable")
    args = parser.parse_args()

    state = DHTState(args.name, args.host, args.port, [parse_endpoint(s) for s in args.seed])
    server = ThreadedServer((args.host, args.port), Handler)
    server.state = state  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.join_network()
    result = state.register_self()
    print(
        f"{args.name} id={state.me.node_id} listening={args.host}:{args.port} "
        f"registered={result.get('ok', False)}",
        flush=True,
    )
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
