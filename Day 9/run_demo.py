"""Run the client here and start two real socket daemon processes."""
import multiprocessing as mp
import os
import secrets
import socket

from secure_rpc import Client, HOST, receive, require, seal, send, serve


def start_daemon(context, processes, role, bob_key, alice_key=None):
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=serve, args=(role, bob_key, child, alice_key), name=role)
    process.start()
    processes.append(process)
    child.close()
    try:
        if not parent.poll(10):
            raise RuntimeError(role + " did not start within 10 seconds")
        return parent.recv()
    finally:
        parent.close()


def main():
    context = mp.get_context("spawn")
    processes = []
    client = None
    # Lab bootstrap only: each daemon receives just its required long-term key(s).
    alice_key, bob_key = secrets.token_bytes(32), secrets.token_bytes(32)
    print("[CLIENT] PID={} | Starting 3-process TCP lab.".format(os.getpid()), flush=True)
    try:
        kdc_port = start_daemon(context, processes, "KDC", bob_key, alice_key)
        server_port = start_daemon(context, processes, "SERVER", bob_key)
        # An unauthenticated request must never reach the resource store.
        with socket.create_connection((HOST, server_port), timeout=5) as sock:
            with sock.makefile("rb") as reader:
                send(sock, {"op": "write", "key": "Balance", "value": 999})
                require(receive(reader).get("error") == "AUTH_FAILED")
        print("[CLIENT] RPC before authentication: AUTH_FAILED (expected).", flush=True)
        print("[CLIENT] Requesting Session Key for Server_Bob from KDC (fresh R_A)...", flush=True)
        client = Client(alice_key, kdc_port, server_port)
        print("[CLIENT] KDC response decrypted | R_A checked | Ticket forwarded.", flush=True)
        print("[CLIENT] Secure Channel Established with Server_Bob!", flush=True)

        nonce = secrets.token_hex(16)
        write = seal(client.key, {"nonce": nonce, "op": "write", "key": "Balance", "value": 500}, "rpc:c2s")
        result = client.exchange(write, nonce)
        require(result.get("ok") is True and result.get("value") == 500)
        print("[CLIENT] Write('Balance', 500) | AES-CBC + HMAC | OK.", flush=True)
        result = client.exchange(write, nonce)
        require(result.get("error") == "REPLAY_DETECTED")
        print("[CLIENT] Exact same packet replayed: REPLAY_DETECTED (expected).", flush=True)

        nonce = secrets.token_hex(16)
        tampered = seal(client.key, {"nonce": nonce, "op": "write", "key": "Balance", "value": 999}, "rpc:c2s")
        raw = bytearray.fromhex(tampered["ciphertext"])
        raw[0] ^= 1
        tampered["ciphertext"] = raw.hex()
        result = client.exchange(tampered, nonce)
        require(result.get("error") == "AUTH_FAILED")
        print("[CLIENT] Ciphertext modified: AUTH_FAILED (expected).", flush=True)

        result = client.rpc("read", "Balance")
        require(result.get("ok") is True and result.get("value") == 500)
        print("[CLIENT] Read('Balance') = 500 | Bad packets did not change data.", flush=True)
        print("[CLIENT] ALL CHECKS PASSED", flush=True)
    finally:
        if client is not None:
            client.close()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join()


if __name__ == "__main__":
    main()
