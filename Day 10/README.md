# Day 10 — Secure Group Multicast & ABAC Simulator

Implementation of the lab on pages 41–45 of `lect10_security_mechanisms.pdf`, following [LAB_DESIGN.md](LAB_DESIGN.md).

## Run

Use Python 3.10+ and install the dependency in a virtual environment:

```sh
cd "Day 10"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run_demo.py
python -m unittest discover -s . -p 'test_*.py' -v
```

The demo starts **one TCP server and eight independent client processes** on localhost, automatically chooses a free port, and closes all processes. It prints its checks and writes `execution_trace.log` beside the script. Use `python run_demo.py --no-trace` to avoid replacing that file. Tests do not overwrite the saved trace. Localhost socket access is required.

The demo explicitly injects **12:00:00 server-local time** so it works during evening study. `serve(registry, ready_pipe, stop_event)` without `fixed_time` uses the actual server clock. Credentials and roles are provisioned by the launcher. Client-supplied `role` or `time` cannot override authorization.

## Files

| File | Purpose |
| --- | --- |
| `secure_group.py` | LKH controller, AES-GCM, policy, framed TCP service, client |
| `run_demo.py` | Repeatable process-based lab and exclusion proofs |
| `test_secure_group.py` | Core security, policy, protocol and demo checks |
| `requirements.txt` | Cryptographic dependency |
| `execution_trace.log` | Captured output from an actual demo run |
| `DESIGN_REPORT.md` | Protocol, counting analysis, evidence and limitations |

## Expected behavior

- Capacity 8, binary tree height 3; each client holds four current path keys.
- A new member cannot decrypt a recorded earlier payload, even when directly trying its new root key against the old AES-GCM ciphertext.
- After member 7 leaves a full tree, five encrypted rekey blocks update the survivors. The departed member cannot recover replacement keys using any of its retained old keys or decrypt the new payload.
- All seven survivors decrypt the new payload. Publication requires an active, authenticated `Publisher`, action `publish`, and server-local time in `[08:00, 18:00]`, inclusive.
- Tampered ciphertext, tampered authenticated metadata, repeated session records and stale rekey epochs are rejected.

This is logical multicast over TCP. The application payload is encrypted once with the root key; its envelope is delivered in authenticated session records to each recipient. It is a teaching simulator with pre-provisioned secrets and memory-only state.
