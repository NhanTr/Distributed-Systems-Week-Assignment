"""Day 9: AES-CBC + HMAC, KDC tickets and a small TCP RPC protocol."""
import hashlib
import hmac
import json
import os
import secrets
import socket
import time

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

HOST = "127.0.0.1"
MAX_FRAME = 65536
TICKET_LIFETIME = 120


class SecurityError(ValueError):
    pass


def encode(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def keys(master, context):
    """Separate AES/HMAC keys, also separating message types and directions."""
    material = HKDF(algorithm=hashes.SHA256(), length=64, salt=None,
                    info=("day9:" + context).encode()).derive(master)
    return material[:32], material[32:]


def seal(master, data, context):
    encryption_key, mac_key = keys(master, context)
    iv = secrets.token_bytes(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(encode(data)) + padder.finalize()
    encryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    packet = {"iv": iv.hex(), "ciphertext": ciphertext.hex()}
    packet["mac"] = hmac.new(mac_key, encode(packet), hashlib.sha256).hexdigest()
    return packet


def unseal(master, packet, context):
    """Authenticate BEFORE decrypting, including IV in the HMAC."""
    try:
        if not isinstance(packet, dict) or set(packet) != {"iv", "ciphertext", "mac"}:
            raise ValueError("shape")
        encryption_key, mac_key = keys(master, context)
        body = {"iv": packet["iv"], "ciphertext": packet["ciphertext"]}
        expected = hmac.new(mac_key, encode(body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, packet["mac"]):
            raise ValueError("mac")
        iv, ciphertext = bytes.fromhex(packet["iv"]), bytes.fromhex(packet["ciphertext"])
        if len(iv) != 16 or not ciphertext or len(ciphertext) % 16:
            raise ValueError("length")
        decryptor = Cipher(algorithms.AES(encryption_key), modes.CBC(iv)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        data = json.loads(unpadder.update(padded) + unpadder.finalize())
        if not isinstance(data, dict):
            raise ValueError("payload")
        return data
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise SecurityError("AUTH_FAILED") from error


def require(condition, reason="AUTH_FAILED"):
    if not condition:
        raise SecurityError(reason)


class KDC:
    def __init__(self, alice_key, bob_key):
        self.alice_key, self.bob_key = alice_key, bob_key

    def issue(self, request):
        require(request.get("client") == "Alice" and request.get("server") == "Bob")
        require(isinstance(request.get("nonce"), str) and bool(request["nonce"]))
        now = time.time()
        ticket = {"client": "Alice", "server": "Bob", "session_id": secrets.token_hex(16),
                  "session_key": secrets.token_hex(32), "issued": now,
                  "expires": now + TICKET_LIFETIME}
        grant = dict(ticket, nonce=request["nonce"], ticket=seal(self.bob_key, ticket, "ticket:Bob"))
        return seal(self.alice_key, grant, "kdc:Alice")


class ResourceServer:
    def __init__(self, bob_key):
        self.bob_key = bob_key
        self.used_tickets = set()
        self.store = {}

    def authenticate(self, ticket_packet, hello_packet):
        ticket = unseal(self.bob_key, ticket_packet, "ticket:Bob")
        require(ticket["client"] == "Alice" and ticket["server"] == "Bob")
        require(ticket["issued"] <= time.time() < ticket["expires"], "TICKET_EXPIRED")
        require(ticket["session_id"] not in self.used_tickets, "TICKET_REPLAY")
        session_key = bytes.fromhex(ticket["session_key"])
        hello = unseal(session_key, hello_packet, "hello")
        require(hello["session_id"] == ticket["session_id"] and bool(hello["nonce"]))
        self.used_tickets.add(ticket["session_id"])
        return PendingAuthentication(self, ticket, session_key, hello["nonce"])


class PendingAuthentication:
    def __init__(self, server, ticket, key, client_nonce):
        self.server, self.ticket, self.key = server, ticket, key
        self.client_nonce = client_nonce
        self.number = secrets.randbits(128)
        self.finished = False
        self.challenge = seal(key, {"challenge": self.number, "nonce": client_nonce,
                                    "session_id": ticket["session_id"]}, "challenge")

    def finish(self, proof_packet):
        require(not self.finished)
        proof = unseal(self.key, proof_packet, "proof")
        require(proof.get("answer") == self.number - 1)
        require(proof.get("session_id") == self.ticket["session_id"])
        require(time.time() < self.ticket["expires"], "TICKET_EXPIRED")
        self.finished = True
        return Session(self.key, self.ticket["expires"], self.server.store)


class Session:
    def __init__(self, key, expires, store):
        self.key, self.expires, self.store = key, expires, store
        self.seen = set()

    def execute(self, packet):
        nonce = None
        try:
            message = unseal(self.key, packet, "rpc:c2s")
            nonce = message.get("nonce")
            require(time.time() < self.expires, "SESSION_EXPIRED")
            require(isinstance(nonce, str) and 0 < len(nonce) <= 128, "INVALID_NONCE")
            require(nonce not in self.seen, "REPLAY_DETECTED")
            self.seen.add(nonce)
            require(message.get("op") in ("read", "write") and
                    isinstance(message.get("key"), str), "INVALID_RPC")
            if message["op"] == "write":
                require("value" in message, "INVALID_RPC")
                self.store[message["key"]] = message["value"]
            result = {"ok": True, "nonce": nonce, "value": self.store.get(message["key"])}
        except SecurityError as error:
            result = {"ok": False, "nonce": nonce, "error": str(error)}
        return seal(self.key, result, "rpc:s2c")


def send(sock, message):
    frame = encode(message) + b"\n"
    if len(frame) > MAX_FRAME:
        raise ValueError("Frame too large")
    sock.sendall(frame)


def receive(reader):
    frame = reader.readline(MAX_FRAME + 1)
    if not frame:
        raise EOFError("Connection closed")
    if len(frame) > MAX_FRAME or not frame.endswith(b"\n"):
        raise ValueError("Invalid frame")
    message = json.loads(frame)
    if not isinstance(message, dict):
        raise ValueError("Expected JSON object")
    return message


def serve(role, key, ready, alice_key=None):
    """One daemon process; port 0 lets the OS allocate an available port."""
    service = KDC(alice_key, key) if role == "KDC" else ResourceServer(key)
    with socket.socket() as listener:
        listener.bind((HOST, 0))
        listener.listen(5)
        ready.send(listener.getsockname()[1])
        ready.close()
        print("[{}] Listening | PID={}".format(role, os.getpid()), flush=True)
        while True:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                with connection.makefile("rb") as reader:
                    try:
                        request = receive(reader)
                        if role == "KDC":
                            send(connection, service.issue(request))
                            print("[KDC] Ticket_Bob Generated | Session Key K_AB Created.", flush=True)
                            continue
                        pending = service.authenticate(request["ticket"], request["hello"])
                        send(connection, pending.challenge)
                        session = pending.finish(receive(reader))
                        send(connection, seal(session.key, {"ready": True, "nonce": pending.client_nonce,
                             "session_id": pending.ticket["session_id"]}, "ready"))
                        print("[SERVER] Ticket decrypted | Challenge R_B - 1 verified.", flush=True)
                        while True:
                            reply = session.execute(receive(reader))
                            send(connection, reply)
                            result = unseal(session.key, reply, "rpc:s2c")
                            print("[SERVER] " + ("HMAC verified | RPC executed." if result["ok"]
                                                  else "Rejected: " + result["error"]), flush=True)
                    except EOFError:
                        pass
                    except (ValueError, KeyError, TypeError, OSError):
                        # Authentication failures have one generic, non-secret error.
                        try:
                            send(connection, {"error": "AUTH_FAILED"})
                        except OSError:
                            pass


class Client:
    def __init__(self, alice_key, kdc_port, server_port):
        request_nonce = secrets.token_hex(16)
        with socket.create_connection((HOST, kdc_port), timeout=5) as sock:
            with sock.makefile("rb") as reader:
                send(sock, {"client": "Alice", "server": "Bob", "nonce": request_nonce})
                grant = unseal(alice_key, receive(reader), "kdc:Alice")
        require(grant["nonce"] == request_nonce and grant["server"] == "Bob" and grant["client"] == "Alice")
        require(grant["issued"] <= time.time() < grant["expires"])
        self.key = bytes.fromhex(grant["session_key"])
        self.sock = socket.create_connection((HOST, server_port), timeout=5)
        self.reader = self.sock.makefile("rb")
        try:
            nonce = secrets.token_hex(16)
            hello = seal(self.key, {"nonce": nonce, "session_id": grant["session_id"]}, "hello")
            send(self.sock, {"ticket": grant["ticket"], "hello": hello})
            challenge = unseal(self.key, receive(self.reader), "challenge")
            require(challenge["nonce"] == nonce and challenge["session_id"] == grant["session_id"])
            send(self.sock, seal(self.key, {"answer": challenge["challenge"] - 1,
                                          "session_id": grant["session_id"]}, "proof"))
            # Confirm that Bob accepted our challenge answer before sending RPCs.
            ready = unseal(self.key, receive(self.reader), "ready")
            require(ready.get("ready") is True and ready.get("nonce") == nonce and
                    ready.get("session_id") == grant["session_id"])
        except Exception:
            self.close()
            raise

    def exchange(self, packet, nonce):
        send(self.sock, packet)
        result = unseal(self.key, receive(self.reader), "rpc:s2c")
        require(result.get("nonce") == nonce or
                (result.get("nonce") is None and result.get("error") == "AUTH_FAILED"))
        return result

    def rpc(self, operation, key, value=None):
        nonce = secrets.token_hex(16)
        packet = seal(self.key, {"op": operation, "key": key, "value": value, "nonce": nonce}, "rpc:c2s")
        return self.exchange(packet, nonce)

    def close(self):
        self.reader.close()
        self.sock.close()
