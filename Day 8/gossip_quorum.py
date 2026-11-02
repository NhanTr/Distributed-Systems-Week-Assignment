"""TCP replicas, push-pull anti-entropy and a single-writer quorum proxy."""
import json
import random
import socket
import socketserver
import threading
from dataclasses import asdict, dataclass

MAX_FRAME = 4 * 1024 * 1024


@dataclass(frozen=True)
class Record:
    value: object
    version_number: int
    is_tombstone: bool = False

    def wire(self):
        return asdict(self)

    @classmethod
    def parse(cls, data):
        record = cls(**data)
        if type(record.version_number) is not int or record.version_number < 1:
            raise ValueError('version_number must be a positive integer')
        if type(record.is_tombstone) is not bool:
            raise ValueError('is_tombstone must be boolean')
        if record.is_tombstone and record.value is not None:
            raise ValueError('tombstone value must be null')
        if not record.is_tombstone and not isinstance(record.value, str):
            raise ValueError('live values must be strings')
        return record


class Store:
    def __init__(self):
        self.records = {}
        self.lock = threading.RLock()

    def snapshot(self):
        with self.lock:
            return {key: r.wire() for key, r in self.records.items()}

    def merge(self, incoming):
        parsed = {key: Record.parse(data) for key, data in incoming.items()}
        with self.lock:
            for key, new in parsed.items():
                old = self.records.get(key)
                if old and old.version_number == new.version_number and old != new:
                    raise ValueError('conflicting equal versions: use only one writer proxy')
            for key, new in parsed.items():
                old = self.records.get(key)
                if old is None or new.version_number > old.version_number:
                    self.records[key] = new


def send_frame(stream, message):
    payload = json.dumps(message, ensure_ascii=False).encode('utf-8') + b'\n'
    if len(payload) > MAX_FRAME:
        raise ValueError('frame exceeds 4 MiB lab limit')
    stream.write(payload)
    stream.flush()


def receive_frame(stream):
    line = stream.readline(MAX_FRAME + 1)
    if not line.endswith(b'\n') or len(line) > MAX_FRAME:
        raise ValueError('missing, incomplete or oversized JSON frame')
    return json.loads(line)


def rpc(address, message, timeout=1.0):
    with socket.create_connection(tuple(address), timeout=timeout) as conn:
        conn.settimeout(timeout)
        with conn.makefile('rwb') as stream:
            send_frame(stream, message)
            response = receive_frame(stream)
    if not response.get('ok'):
        raise ValueError(response.get('error', 'RPC failed'))
    return response


class Replica:
    def __init__(self, name, peers, emit, gossip_enabled=True, interval=2.0):
        self.name, self.peers, self.emit = name, peers, emit
        self.store = Store()
        self.enabled = threading.Event()
        if gossip_enabled:
            self.enabled.set()
        self.closed = threading.Event()
        self.interval = interval
        self.rng = random.Random(name)

    def sync(self, peer):
        if peer == self.name or peer not in self.peers:
            raise ValueError('unknown peer or self peer')
        # Snapshot is taken without holding a lock across network I/O.
        response = rpc(self.peers[peer], {'op': 'exchange', 'state': self.store.snapshot()})
        self.store.merge(response['state'])
        self.emit(f'[GOSSIP] {self.name} <-> {peer} Push-Pull completed')
        return {'ok': True, 'state': self.store.snapshot()}

    def dispatch(self, message):
        op = message['op']
        if op == 'ping':
            return {'ok': True, 'name': self.name}
        if op == 'snapshot':
            return {'ok': True, 'state': self.store.snapshot()}
        if op == 'get':
            return {'ok': True, 'record': self.store.snapshot().get(message['key'])}
        if op == 'put':
            key = message['key']
            self.store.merge({key: message['record']})
            current = self.store.snapshot()[key]
            accepted = current == message['record']
            self.emit(f'[{self.name}] Write ACK={accepted} key={key!r} Ver {current["version_number"]} tombstone={current["is_tombstone"]}')
            return {'ok': True, 'accepted': accepted, 'record': current}
        if op == 'exchange':
            self.store.merge(message['state'])
            self.emit(f'[GOSSIP] {self.name} merged incoming state')
            return {'ok': True, 'state': self.store.snapshot()}
        if op == 'sync':
            return self.sync(message['peer'])
        if op == 'gossip':
            if message['enabled']:
                self.enabled.set()
            else:
                self.enabled.clear()
            return {'ok': True}
        raise ValueError('unknown operation')

    def gossip_loop(self):
        peers = [n for n in self.peers if n != self.name]
        while not self.closed.wait(self.interval):
            if not self.enabled.is_set():
                continue
            peer = self.rng.choice(peers)
            try:
                self.sync(peer)
            except (OSError, ValueError) as error:
                self.emit(f'[GOSSIP] {self.name} -> {peer} unavailable: {error}')


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(2)
        try:
            response = self.server.replica.dispatch(receive_frame(self.rfile))
        except (OSError, ValueError, KeyError, TypeError) as error:
            response = {'ok': False, 'error': str(error)}
        try:
            send_frame(self.wfile, response)
        except OSError:
            pass  # Requester may have timed out; the operation may already be applied.


class TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve_replica(listener, name, peers, log_path, gossip_enabled, interval):
    log_lock = threading.Lock()
    with open(log_path, 'w', encoding='utf-8', buffering=1) as log:
        def emit(message):
            with log_lock:
                log.write(message + '\n')
        replica = Replica(name, peers, emit, gossip_enabled, interval)
        server = TCPServer(listener.getsockname(), Handler, bind_and_activate=False)
        server.socket.close()
        server.socket = listener
        server.server_address = listener.getsockname()
        server.replica = replica
        worker = threading.Thread(target=replica.gossip_loop, daemon=True)
        worker.start()
        emit(f'[{name}] Listening on {server.server_address}; gossip interval={interval}s')
        try:
            server.serve_forever(poll_interval=.1)
        finally:
            replica.closed.set()
            server.server_close()


class QuorumError(RuntimeError):
    """Insufficient votes; a failed write may have partially applied."""


def validate_quorums(n, nr, nw):
    if any(type(v) is not int for v in (n, nr, nw)):
        raise ValueError('quorums must be integers')
    if not (1 <= nr <= n and 1 <= nw <= n and nr + nw > n and 2 * nw > n):
        raise ValueError('require 1<=NR,NW<=N, NR+NW>N and 2*NW>N')


class Proxy:
    """Exactly one proxy owns write ordering; callers can use multiple threads."""
    def __init__(self, peers, nr=3, nw=3, emit=lambda line: None):
        validate_quorums(len(peers), nr, nw)
        self.peers = dict(peers)
        self.nr, self.nw, self.emit = nr, nw, emit
        self.lock = threading.RLock()
        self.version = 0

    def _query(self, key, needed, nodes=None):
        names = list(self.peers) if nodes is None else list(nodes)
        if len(set(names)) != len(names) or any(n not in self.peers for n in names):
            raise ValueError('targets must be distinct known replicas')
        answers = []
        for name in names:
            try:
                record = rpc(self.peers[name], {'op': 'get', 'key': key})['record']
                answers.append((name, record))
            except (OSError, ValueError) as error:
                self.emit(f'[CLIENT] {name} unavailable: {error}')
            if len(answers) >= needed:
                return answers
        raise QuorumError(f'need {needed} responses, obtained {len(answers)}')

    def read(self, key, nodes=None):
        with self.lock:
            answers = self._query(key, self.nr, nodes)
            records = [r for _, r in answers if r is not None]
            newest = max(records, key=lambda r: r['version_number']) if records else None
            self.emit(f'[CLIENT] READ {key!r}, NR={self.nr}, replies={answers}, selected={newest}')
            return None if newest is None or newest['is_tombstone'] else newest['value']

    def write(self, key, value):
        if not isinstance(value, str):
            raise ValueError('value must be a string')
        return self._write(key, value, False)

    def delete(self, key):
        return self._write(key, None, True)

    def _write(self, key, value, tombstone):
        with self.lock:
            # Read intersection discovers earlier versions, including after proxy restart.
            answers = self._query(key, self.nr)
            self.version = max([self.version] + [r['version_number'] for _, r in answers if r]) + 1
            record = Record(value, self.version, tombstone).wire()
            self.emit(f'[CLIENT] {"DELETE" if tombstone else "WRITE"} key={key!r} value={value!r} NW={self.nw} Ver {self.version}')
            acks = []
            for name, address in self.peers.items():
                try:
                    reply = rpc(address, {'op': 'put', 'key': key, 'record': record})
                    if reply['accepted']:
                        acks.append(name)
                        self.emit(f'[{name}] Write ACK Ver {self.version}')
                except (OSError, ValueError) as error:
                    self.emit(f'[CLIENT] {name} unavailable: {error}')
                if len(acks) >= self.nw:
                    self.emit(f'[SYSTEM] Write Committed (NW={self.nw}), ACKs={acks}')
                    return self.version
            raise QuorumError(f'write indeterminate: {len(acks)}/{self.nw} ACKs; partial state may propagate')
