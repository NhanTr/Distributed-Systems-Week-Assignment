"""Tests cover security failures as well as the successful lab flow."""
import copy
import secrets
import subprocess
import sys
import time
import unittest
from pathlib import Path

from secure_rpc import KDC, ResourceServer, SecurityError, seal, unseal


class CryptoTests(unittest.TestCase):
    def setUp(self):
        self.key = secrets.token_bytes(32)

    def test_round_trip_hides_plaintext_and_uses_fresh_iv(self):
        data = {"message": "Xin chào", "value": 500}
        first = seal(self.key, data, "rpc:c2s")
        self.assertEqual(unseal(self.key, first, "rpc:c2s"), data)
        self.assertNotIn("Xin chào", str(first))
        self.assertNotEqual(first["iv"], seal(self.key, data, "rpc:c2s")["iv"])

    def test_tampering_any_field_is_rejected(self):
        packet = seal(self.key, {"value": 500}, "rpc:c2s")
        for field in ("iv", "ciphertext", "mac"):
            with self.subTest(field=field):
                bad = copy.deepcopy(packet)
                raw = bytearray.fromhex(bad[field])
                raw[0] ^= 1
                bad[field] = raw.hex()
                with self.assertRaises(SecurityError):
                    unseal(self.key, bad, "rpc:c2s")

    def test_wrong_key_direction_and_malformed_packet_rejected(self):
        packet = seal(self.key, {"value": 500}, "rpc:c2s")
        for key, context, data in (
            (secrets.token_bytes(32), "rpc:c2s", packet),
            (self.key, "rpc:s2c", packet),
            (self.key, "rpc:c2s", {}),
            (self.key, "rpc:c2s", dict(packet, iv="xyz")),
        ):
            with self.assertRaises(SecurityError):
                unseal(key, data, context)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.ka, self.kb = secrets.token_bytes(32), secrets.token_bytes(32)
        self.kdc = KDC(self.ka, self.kb)
        self.server = ResourceServer(self.kb)
        response = self.kdc.issue({"client": "Alice", "server": "Bob", "nonce": "request-1"})
        self.grant = unseal(self.ka, response, "kdc:Alice")
        self.key = bytes.fromhex(self.grant["session_key"])

    def start(self, ticket=None):
        hello = seal(self.key, {"nonce": "hello-1", "session_id": self.grant["session_id"]}, "hello")
        return self.server.authenticate(ticket or self.grant["ticket"], hello)

    def finish(self, pending, answer=None):
        challenge = unseal(self.key, pending.challenge, "challenge")
        proof = seal(self.key, {
            "answer": challenge["challenge"] - 1 if answer is None else answer,
            "session_id": self.grant["session_id"],
        }, "proof")
        return pending.finish(proof)

    def test_kdc_binds_nonce_identity_and_ticket(self):
        self.assertEqual(self.grant["nonce"], "request-1")
        ticket = unseal(self.kb, self.grant["ticket"], "ticket:Bob")
        self.assertEqual(ticket["client"], "Alice")
        self.assertEqual(ticket["server"], "Bob")
        self.assertEqual(ticket["session_key"], self.grant["session_key"])
        with self.assertRaises(SecurityError):
            self.kdc.issue({"client": "Eve", "server": "Bob", "nonce": "n"})

    def test_wrong_challenge_cannot_authenticate(self):
        with self.assertRaises(SecurityError):
            self.finish(self.start(), answer=-1)

    def test_ticket_expiry_wrong_server_and_wrong_key_rejected(self):
        ticket = unseal(self.kb, self.grant["ticket"], "ticket:Bob")
        for updates in ({"expires": time.time() - 1}, {"server": "Eve"}):
            with self.subTest(updates=updates), self.assertRaises(SecurityError):
                self.start(seal(self.kb, dict(ticket, **updates), "ticket:Bob"))
        with self.assertRaises(SecurityError):
            self.start(seal(self.ka, ticket, "ticket:Bob"))

    def test_reusing_ticket_cannot_create_fresh_replay_cache(self):
        self.finish(self.start())
        with self.assertRaises(SecurityError):
            self.start()

    def test_secure_write_read_replay_and_tamper_have_no_side_effect(self):
        session = self.finish(self.start())
        write = seal(self.key, {"nonce": "one", "op": "write", "key": "Balance", "value": 500}, "rpc:c2s")
        result = unseal(self.key, session.execute(write), "rpc:s2c")
        self.assertEqual(result, {"ok": True, "nonce": "one", "value": 500})
        replay = unseal(self.key, session.execute(write), "rpc:s2c")
        self.assertEqual(replay["error"], "REPLAY_DETECTED")
        bad = seal(self.key, {"nonce": "two", "op": "write", "key": "Balance", "value": 999}, "rpc:c2s")
        bad["mac"] = "00" * 32
        self.assertEqual(unseal(self.key, session.execute(bad), "rpc:s2c")["error"], "AUTH_FAILED")
        read = seal(self.key, {"nonce": "two", "op": "read", "key": "Balance"}, "rpc:c2s")
        self.assertEqual(unseal(self.key, session.execute(read), "rpc:s2c")["value"], 500)

    def test_expired_session_and_invalid_rpc_are_rejected(self):
        session = self.finish(self.start())
        for message in ({"nonce": "", "op": "read", "key": "Balance"},
                        {"nonce": "n1", "op": "delete", "key": "Balance"}):
            result = unseal(self.key, session.execute(seal(self.key, message, "rpc:c2s")), "rpc:s2c")
            self.assertFalse(result["ok"])
        session.expires = time.time() - 1
        result = unseal(self.key, session.execute(seal(self.key, {"nonce": "n2", "op": "read", "key": "Balance"}, "rpc:c2s")), "rpc:s2c")
        self.assertEqual(result["error"], "SESSION_EXPIRED")


class DemoTests(unittest.TestCase):
    def test_real_three_process_tcp_demo(self):
        result = subprocess.run([sys.executable, "run_demo.py"], cwd=Path(__file__).parent,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for expected in ("Write('Balance', 500)", "REPLAY_DETECTED", "AUTH_FAILED",
                         "Read('Balance') = 500", "ALL CHECKS PASSED"):
            self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
