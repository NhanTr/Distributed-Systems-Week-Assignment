"""LKH + AES-256-GCM + ABAC educational localhost group service.

Node 1 is the root; leaves are capacity..2*capacity-1. Versions are the
membership epoch in which a node key was generated. Never log key bytes.
"""
import hashlib
import hmac
import json
import secrets
import socket
import struct
import threading
from datetime import datetime, time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HOST = '127.0.0.1'
MAX_FRAME = 1024 * 1024


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def seal(key, plaintext, header):
    nonce = secrets.token_bytes(12)
    return {'nonce': nonce.hex(),
            'ciphertext': AESGCM(key).encrypt(nonce, plaintext, encoded(header)).hex()}


def open_box(key, box, header):
    return AESGCM(key).decrypt(bytes.fromhex(box['nonce']),
                               bytes.fromhex(box['ciphertext']), encoded(header))


def allowed(active, role, action, moment):
    return (active is True and role == 'Publisher' and action == 'publish'
            and isinstance(moment, time) and moment.tzinfo is None
            and time(8) <= moment <= time(18))


def path_for(leaf):
    path = []
    while leaf:
        path.append(leaf)
        leaf //= 2
    return path


def block_header(update, block):
    return {k: update[k] for k in ('kind', 'epoch')} | {
        k: block[k] for k in ('node', 'wrapper', 'version')}


def recover(keys, update):
    """Try every available wrapper; also models a revoked member's knowledge."""
    result = dict(keys)
    for block in update['blocks']:
        wrapper = (block['wrapper'], block['version'])
        if wrapper in result:
            try:
                key = open_box(result[wrapper], block['box'], block_header(update, block))
            except InvalidTag:
                continue
            result[(block['node'], update['epoch'])] = key
    return result


class Controller:
    def __init__(self, capacity=8):
        if type(capacity) is not int or capacity < 2 or capacity & (capacity - 1):
            raise ValueError('capacity must be a power of two >= 2')
        self.capacity = capacity
        self.epoch = 0
        self.members = {}
        self.keys = {node: (0, secrets.token_bytes(32))
                     for node in range(1, 2 * capacity)}

    def occupied(self, node):
        return any(node in path_for(leaf) for leaf in self.members.values())

    def wrap(self, update, node, wrapper, version, key):
        block = {'node': node, 'wrapper': wrapper, 'version': version}
        block['box'] = seal(key, self.keys[node][1], block_header(update, block))
        update['blocks'].append(block)

    def join(self, identity):
        if not isinstance(identity, str) or not identity or identity in self.members:
            raise ValueError('invalid or duplicate identity')
        free = [n for n in range(self.capacity, 2 * self.capacity)
                if n not in self.members.values()]
        if not free:
            raise ValueError('group full')
        leaf = free[0]
        path = path_for(leaf)
        self.epoch += 1
        update = {'kind': 'JOIN', 'epoch': self.epoch, 'blocks': []}
        # Only old members get these blocks. The entrant receives a NEW path.
        for node in path:
            version, old = self.keys[node]
            had_members = self.occupied(node)
            self.keys[node] = (self.epoch, secrets.token_bytes(32))
            if had_members:
                self.wrap(update, node, node, version, old)
        self.members[identity] = leaf
        bootstrap = {'epoch': self.epoch, 'leaf': leaf,
                     'keys': {str(n): [self.keys[n][0], self.keys[n][1].hex()] for n in path}}
        return update, bootstrap

    def leave(self, identity):
        if identity not in self.members:
            raise ValueError('inactive identity')
        leaf = self.members.pop(identity)
        self.epoch += 1
        update = {'kind': 'LEAVE', 'epoch': self.epoch, 'blocks': []}
        for node in path_for(leaf):
            self.keys[node] = (self.epoch, secrets.token_bytes(32))
            if node < self.capacity:
                for child in (node * 2, node * 2 + 1):
                    if self.occupied(child):
                        version, key = self.keys[child]
                        self.wrap(update, node, child, version, key)
        return update

    def encrypt(self, text):
        header = {'kind': 'payload', 'epoch': self.epoch, 'node': 1,
                  'version': self.keys[1][0]}
        return {'header': header, 'box': seal(self.keys[1][1], text.encode(), header)}


class MemberState:
    def __init__(self, bootstrap):
        self.epoch = bootstrap['epoch']
        self.path = path_for(bootstrap['leaf'])
        self.keys = {int(n): (v, bytes.fromhex(k)) for n, (v, k) in bootstrap['keys'].items()}
        if set(self.keys) != set(self.path):
            raise ValueError('bootstrap must contain only own path')

    def apply(self, update):
        if update['epoch'] != self.epoch + 1 or update['kind'] not in ('JOIN', 'LEAVE'):
            raise ValueError('stale or missing epoch')
        candidate = dict(self.keys)
        for block in update['blocks']:
            wrapper = candidate.get(block['wrapper'])
            if block['node'] in self.path and wrapper and wrapper[0] == block['version']:
                key = open_box(wrapper[1], block['box'], block_header(update, block))
                if len(key) != 32:
                    raise ValueError('invalid AES-256 key')
                candidate[block['node']] = (update['epoch'], key)
        if candidate[1][0] != update['epoch']:
            raise ValueError('no current root recovered')
        self.keys = candidate
        self.epoch = update['epoch']

    def decrypt(self, envelope):
        header = envelope['header']
        if header['epoch'] != self.epoch or header['version'] != self.keys[1][0]:
            raise ValueError('payload belongs to another epoch')
        return open_box(self.keys[1][1], envelope['box'], header).decode()


def read_exact(sock, count):
    data = bytearray()
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise EOFError('connection closed')
        data.extend(part)
    return bytes(data)


def send(sock, value):
    data = encoded(value)
    if len(data) > MAX_FRAME:
        raise ValueError('frame too large')
    sock.sendall(struct.pack('!I', len(data)) + data)


def receive(sock):
    size, = struct.unpack('!I', read_exact(sock, 4))
    if not 0 < size <= MAX_FRAME:
        raise ValueError('invalid frame length')
    value = json.loads(read_exact(sock, size))
    if not isinstance(value, dict):
        raise ValueError('frame must be an object')
    return value


class Channel:
    """Direction-bound, sequenced AEAD records after challenge authentication."""
    def __init__(self, sock, key, outbound, inbound):
        self.sock, self.key = sock, key
        self.outbound, self.inbound = outbound, inbound
        self.sent = self.received = 0

    def send(self, value):
        header = {'kind': 'session', 'direction': self.outbound, 'seq': self.sent}
        send(self.sock, {'header': header, 'box': seal(self.key, encoded(value), header)})
        self.sent += 1

    def receive(self):
        packet = receive(self.sock)
        header = {'kind': 'session', 'direction': self.inbound, 'seq': self.received}
        if packet['header'] != header:
            raise ValueError('session replay or wrong direction')
        value = json.loads(open_box(self.key, packet['box'], header))
        if not isinstance(value, dict):
            raise ValueError('session record must be an object')
        self.received += 1
        return value


def auth_material(secret, identity, challenge, nonce):
    transcript = encoded({'identity': identity, 'challenge': challenge, 'nonce': nonce})
    proof = hmac.digest(secret, b'proof:' + transcript, hashlib.sha256).hex()
    key = hmac.digest(secret, b'session:' + transcript, hashlib.sha256)
    return proof, key


def serve(registry, ready, stop, fixed_time=None, capacity=8):
    """Run a localhost key server. fixed_time is an explicit lab clock override.

    Registry is provisioned by the launcher, never populated from client roles.
    A single lock orders all joins, leaves, broadcasts and replies. TCP preserves
    that order, so clients process a rekey before the next epoch's payload.
    """
    group = Controller(capacity)
    lock = threading.RLock()
    sessions, sockets, threads = {}, set(), []
    clock = (lambda: time.fromisoformat(fixed_time)) if fixed_time else (lambda: datetime.now().time())

    def broadcast(event):
        for channel in list(sessions.values()):
            try:
                channel.send(event)
            except OSError:
                # shutdown wakes recv in another thread; close alone can leave
                # that thread blocked on a descriptor already marked closed.
                try:
                    channel.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                # Its handler owns close(); do not invalidate a descriptor
                # while another thread's timed recv is polling it.

    def remove(identity):
        if identity in sessions:
            del sessions[identity]
            update = group.leave(identity)
            broadcast({'event': 'rekey', 'update': update})
            return update
        return None

    def handle(sock):
        identity, channel = None, None
        joined = False
        try:
            challenge = secrets.token_hex(32)
            send(sock, {'challenge': challenge})
            hello = receive(sock)
            identity = hello.get('identity')
            credential = registry.get(identity) if isinstance(identity, str) else None
            nonce = hello.get('nonce')
            if not credential or not isinstance(nonce, str) or len(nonce) != 64:
                raise ValueError('authentication failed')
            proof, key = auth_material(credential['secret'], identity, challenge, nonce)
            if not isinstance(hello.get('proof'), str) or not hmac.compare_digest(proof, hello['proof']):
                raise ValueError('authentication failed')
            channel = Channel(sock, key, 's2c', 'c2s')
            with lock:
                try:
                    update, bootstrap = group.join(identity)
                except ValueError as exc:
                    channel.send({'error': str(exc)})
                    return
                # Existing members get old-key updates before entrant registration.
                broadcast({'event': 'rekey', 'update': update})
                sessions[identity] = channel
                joined = True
                header = {'kind': 'bootstrap', 'identity': identity, 'epoch': group.epoch}
                channel.send({'event': 'joined', 'header': header,
                              'box': seal(credential['secret'], encoded(bootstrap), header)})
            while not stop.is_set():
                request = channel.receive()
                with lock:
                    op = request.get('op')
                    active = sessions.get(identity) is channel
                    if op == 'sync':
                        result = {'ok': True, 'epoch': group.epoch}
                    elif op == 'leave' and active:
                        update = remove(identity)
                        result = {'ok': True, 'update': update}
                    elif (allowed(active, credential['role'], op, clock())
                          and isinstance(request.get('text'), str)
                          and len(request['text'].encode()) <= 4096):
                        # Exactly ONE application encryption; identical envelope to all.
                        envelope = group.encrypt(request['text'])
                        broadcast({'event': 'payload', 'envelope': envelope})
                        result = {'ok': True, 'envelope': envelope}
                    else:
                        result = {'error': 'DENIED'}
                    channel.send({'event': 'reply', **result})
        except (OSError, EOFError, ValueError, KeyError, TypeError, InvalidTag):
            pass  # Fail closed: no malformed or unauthenticated request mutates state.
        finally:
            with lock:
                if joined and sessions.get(identity) is channel:
                    remove(identity)
                sockets.discard(sock)
            sock.close()

    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, 0))
        listener.listen(32)
        listener.settimeout(0.2)
        ready.send(listener.getsockname()[1])
        ready.close()
        try:
            while not stop.is_set():
                try:
                    sock, _ = listener.accept()
                except socket.timeout:
                    continue
                sock.settimeout(30)
                with lock:
                    sockets.add(sock)
                worker = threading.Thread(target=handle, args=(sock,))
                threads.append(worker)
                worker.start()
        finally:
            with lock:
                for sock in list(sockets):
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    # The handler closes its socket after recv wakes up.
            for worker in threads:
                worker.join()


class Client:
    """A sequential client; request() drains ordered rekeys/payloads before reply."""
    def __init__(self, identity, secret, port):
        self.sock = socket.create_connection((HOST, port), timeout=5)
        self.messages, self.envelopes, self.updates = [], [], []
        try:
            challenge = receive(self.sock)['challenge']
            nonce = secrets.token_hex(32)
            proof, key = auth_material(secret, identity, challenge, nonce)
            send(self.sock, {'identity': identity, 'nonce': nonce, 'proof': proof})
            self.channel = Channel(self.sock, key, 'c2s', 's2c')
            result = self.channel.receive()
            if result.get('event') != 'joined':
                raise ValueError(result.get('error', 'authentication failed'))
            self.state = MemberState(json.loads(open_box(secret, result['box'], result['header'])))
        except BaseException:
            self.sock.close()
            raise

    def process(self, event):
        if event['event'] == 'rekey':
            self.state.apply(event['update'])
            self.updates.append(event['update'])
        elif event['event'] == 'payload':
            self.messages.append(self.state.decrypt(event['envelope']))
            self.envelopes.append(event['envelope'])
        else:
            raise ValueError('unexpected event')

    def request(self, op, **attributes):
        self.channel.send({'op': op, **attributes})
        while True:
            event = self.channel.receive()
            if event['event'] == 'reply':
                return event
            self.process(event)

    def close(self):
        self.sock.close()
