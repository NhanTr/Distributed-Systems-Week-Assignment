"""Deterministic three-replica simulator for Monotonic Reads and Read-Your-Writes.

Network delivery uses logical ticks. Stores retain operation history so a proxy
can fetch missing writes before serving a session on a lagging replica.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Callable

Vector = tuple[int, int, int]
ZERO: Vector = (0, 0, 0)
SERVER_IDS = ('S1', 'S2', 'S3')


def dominates(actual: tuple[int, ...], required: tuple[int, ...]) -> bool:
    """True iff every actual component includes the required write prefix."""
    if len(actual) != len(required):
        raise ValueError('Vector lengths must match')
    return all(a >= r for a, r in zip(actual, required))


def merge(left: Vector, right: Vector) -> Vector:
    """Componentwise maximum of two three-replica contexts."""
    return tuple(max(a, b) for a, b in zip(left, right))


def require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')


class UnavailableError(RuntimeError):
    """A target or the history required to serve a session is unavailable."""


@dataclass(frozen=True)
class Write:
    origin: int
    sequence: int
    key: str
    value: str
    vector: Vector

    @property
    def identity(self) -> tuple[int, int]:
        return self.origin, self.sequence

    @property
    def rank(self) -> tuple[int, int, int]:
        # Logical ordering respects causal successors; origin breaks concurrency
        # ties deterministically. No wall-clock timestamp is needed.
        return sum(self.vector), self.origin, self.sequence


@dataclass
class Replica:
    server_id: str
    clock: Vector = ZERO
    online: bool = True
    values: dict[str, Write] = field(default_factory=dict)
    history: dict[tuple[int, int], Write] = field(default_factory=dict)
    pending: dict[tuple[int, int], Write] = field(default_factory=dict)

    def receive(self, write: Write) -> None:
        """Buffer gaps; advance a clock only when the whole prefix is applied."""
        if write.identity in self.history:
            return
        self.pending[write.identity] = write
        while True:
            ready = [w for w in self.pending.values()
                     if self.clock[w.origin] + 1 == w.sequence
                     and all(self.clock[i] >= w.vector[i]
                             for i in range(3) if i != w.origin)]
            if not ready:
                return
            for item in sorted(ready, key=lambda w: w.rank):
                self.pending.pop(item.identity)
                self.history[item.identity] = item
                self.clock = merge(self.clock, item.vector)
                previous = self.values.get(item.key)
                if previous is None or item.rank > previous.rank:
                    self.values[item.key] = item


class Cluster:
    """Three in-memory stores and a delayed, reliable, retrying message queue."""

    def __init__(self, replication_delay: int = 5,
                 emit: Callable[[str], None] | None = None) -> None:
        if type(replication_delay) is not int or replication_delay < 0:
            raise ValueError('Replication delay must be a nonnegative integer')
        self.nodes = {name: Replica(name) for name in SERVER_IDS}
        self.replication_delay = replication_delay
        self.now = 0
        self.emit = emit or (lambda line: None)
        self._queue: list[tuple[int, int, str, Write]] = []
        self._message_id = 0

    def node(self, server_id: str) -> Replica:
        if server_id not in self.nodes:
            raise ValueError(f'Unknown server: {server_id}')
        node = self.nodes[server_id]
        if not node.online:
            raise UnavailableError(f'{server_id} is offline')
        return node

    def set_online(self, server_id: str, online: bool) -> None:
        if server_id not in self.nodes:
            raise ValueError(f'Unknown server: {server_id}')
        self.nodes[server_id].online = online
        self.emit(f'[SYSTEM] {server_id} is {"online" if online else "offline"}')

    def raw_read(self, server_id: str, key: str) -> str | None:
        """Diagnostic read that deliberately bypasses session validation."""
        require_text(key, 'Key')
        item = self.node(server_id).values.get(key)
        return item.value if item else None

    def write(self, server_id: str, key: str, value: str) -> Write:
        require_text(key, 'Key')
        if not isinstance(value, str):
            raise ValueError('Value must be a string')
        node = self.node(server_id)
        origin = SERVER_IDS.index(server_id)
        vector = list(node.clock)
        vector[origin] += 1
        write = Write(origin, vector[origin], key, value, tuple(vector))
        node.receive(write)
        for target in SERVER_IDS:
            if target != server_id:
                self._message_id += 1
                heapq.heappush(self._queue, (self.now + self.replication_delay,
                                            self._message_id, target, write))
        return write

    def advance(self, ticks: int = 1) -> None:
        """Deliver due messages; retry an offline recipient on the next tick."""
        if type(ticks) is not int or ticks < 0:
            raise ValueError('Ticks must be a nonnegative integer')
        end = self.now + ticks
        while self._queue and self._queue[0][0] <= end:
            due, number, target, write = heapq.heappop(self._queue)
            self.now = due
            node = self.nodes[target]
            if node.online:
                node.receive(write)
                self.emit(f'[NETWORK t={due}] Delivered {write.identity} to {target}; '
                          f'clock={node.clock}')
            else:
                heapq.heappush(self._queue, (due + 1, number, target, write))
        self.now = end

    def synchronize(self, server_id: str, required: Vector) -> None:
        """Fetch missing write prefixes from any reachable peer, or block safely.

        One call models a stalled request and synchronous peer fetch. If history
        cannot be fetched, raise rather than return stale data or wait forever.
        """
        target = self.node(server_id)
        if dominates(target.clock, required):
            self.emit(f'[PROXY] Validation passed: {server_id} {target.clock} >= {required}')
            return
        self.emit(f'[PROXY] Validation failed: {server_id} {target.clock} does not '
                  f'dominate {required}; stalling request')
        missing: dict[tuple[int, int], tuple[str, Write]] = {}
        for source in self.nodes.values():
            if source.online and source is not target:
                for identity, write in source.history.items():
                    if identity not in target.history and write.sequence <= required[write.origin]:
                        missing.setdefault(identity, (source.server_id, write))
        for source_id, write in sorted(missing.values(), key=lambda pair: pair[1].rank):
            self.emit(f'[PROXY] Fetching write {write.identity} '
                      f'{write.key}={write.value!r} from {source_id} -> {server_id}')
            target.receive(write)
        if not dominates(target.clock, required):
            self.emit(f'[PROXY] Blocked: required history unreachable; '
                      f'{server_id} clock={target.clock}')
            raise UnavailableError(f'{server_id} cannot reach required session context {required}')
        self.emit(f'[{server_id}] State updated to {target.clock}; request may proceed')


@dataclass
class Session:
    v_read: Vector = ZERO
    v_write: Vector = ZERO


class SessionProxy:
    """Session-aware entry point; callers explicitly select a destination store."""

    def __init__(self, cluster: Cluster) -> None:
        self.cluster = cluster
        self._sessions: dict[str, Session] = {}

    def session(self, client_id: str) -> Session:
        require_text(client_id, 'Client ID')
        return self._sessions.setdefault(client_id, Session())

    def read(self, client_id: str, server_id: str, key: str) -> str | None:
        require_text(key, 'Key')
        session = self.session(client_id)
        required = merge(session.v_read, session.v_write)
        self.cluster.synchronize(server_id, required)
        node = self.cluster.node(server_id)
        value = self.cluster.raw_read(server_id, key)
        session.v_read = merge(session.v_read, node.clock)
        self.cluster.emit(f'[{server_id}] Read {key} -> {value!r}; '
                          f'client={client_id} V_read={session.v_read}')
        return value

    def write(self, client_id: str, server_id: str, key: str, value: str) -> Write:
        require_text(key, 'Key')
        if not isinstance(value, str):
            raise ValueError('Value must be a string')
        session = self.session(client_id)
        # Keeping prior session history before a write also avoids losing causal
        # dependencies if the client changes its destination between operations.
        self.cluster.synchronize(server_id, merge(session.v_read, session.v_write))
        write = self.cluster.write(server_id, key, value)
        session.v_write = merge(session.v_write, write.vector)
        self.cluster.emit(f'[{server_id}] Write ACK {key}={value!r}; '
                          f'client={client_id} V_write={session.v_write}')
        return write
