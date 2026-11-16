# Secure Group Multicast & ABAC Simulator - proposed design

Status: implemented following the user's instruction to complete the lab according to this solution. See `README.md`, `DESIGN_REPORT.md`, and `execution_trace.log`.

## Purpose and constraints

Implement the lab on PDF pages 41-45 as a runnable educational system, matching the earlier Python labs. Keep the lecture PDF unchanged. Deliver source code, a repeatable demo, tests, a captured execution trace, and a design report in `Day 10`.

## Approaches

1. **Recommended: Python server and separate clients over TCP.** Directly meets the socket/process requirement and can reuse the Python `cryptography` dependency already used by Day 9. Use real AES-256-GCM rather than simulated encryption.
2. **In-memory model only.** Simpler to test, but insufficient as the sole deliverable because the PDF explicitly requires TCP client processes. Use an in-memory core only as a component of option 1.
3. **Java or Go implementation.** Both are allowed by the assignment, but would add a different toolchain without a requirement that benefits from it.

Language and the following engineering details are recommendations, not choices already confirmed by the user.

## Architecture

- A key-server process owns a capacity-8 complete binary tree of height 3, an active membership registry, node keys, and monotonically increasing group epochs. Tree capacity is configurable to powers of two for scaling checks. Reject duplicate identities and full-capacity joins.
- Each member has an individual, pre-provisioned 256-bit secret. A server-owned registry supplies its role; clients cannot grant themselves Publisher rights.
- Separate client processes connect over localhost TCP with bounded, length-framed JSON messages. Authenticate the session using the member's individual secret and fresh challenge-response; bind subsequent sensitive requests to that session. Protect bootstrap key delivery under the individual secret.
- Each client stores its leaf key and current ancestor keys only. Rekey listeners apply authenticated blocks bottom-up. Epochs prevent replay of obsolete membership updates.
- Serialize membership operations and publication to avoid sending a payload under an epoch whose rekey has not been processed. Use acknowledgements or ordered per-client delivery as appropriate.
- Logical multicast encrypts the application payload once, then distributes the same envelope over connected TCP streams. This emulates multicast; it is not IP multicast.

## Rekeying and encryption

- Generate 32-byte independent keys using an OS-backed cryptographic random generator.
- Use AES-256-GCM for both key wrapping and payloads. Use fresh 96-bit nonces and bind message type, epoch, node IDs, and wrapper-key versions as authenticated metadata.
- Join: generate new keys on the entrant's ancestor path. Existing members receive updates protected by applicable old keys; deliver the entrant's new path under its own individual secret. Never give the entrant previous ancestor keys.
- Leave: remove the member, replace its ancestor keys, and wrap replacements under surviving child-subtree keys bottom-up. The departed member may keep all its old keys; the exclusion proof must still hold.
- Count encrypted rekey blocks separately from TCP deliveries and the entrant bootstrap. A fully populated capacity-8 tree has at most `2h=6` leave blocks; the selected bottom-up algorithm uses `2h-1=5` after one member leaves. Empty subtrees can reduce that count further.

## ABAC

Default deny. Permit publication only if the authenticated subject is active, its server-assigned role is exactly `Publisher`, the requested action is `publish`, and server-local time is within `[08:00:00, 18:00:00]` inclusive. Malformed attributes/actions fail closed. Clients cannot supply their own authorization time.

Inject the server clock in tests and the deterministic demo so the lab can be verified at night. Explain this explicitly in the trace; normal operation uses the actual server clock.

## Demo and meaningful verification

1. Start a key server and multiple independent TCP client processes, including Publisher and Subscriber roles.
2. Record an encrypted message before an entrant joins; prove the entrant cannot decrypt it using its received keys.
3. Verify survivors and the entrant receive the current message after joining.
4. Retain a departing client's complete old key set; prove it cannot recover replacement keys or decrypt later traffic.
5. Verify all remaining members decrypt the new payload correctly.
6. Reject non-Publisher publication, inactive identities, unsupported actions, and out-of-hours requests. Check the exact 08:00 and 18:00 boundaries.
7. Reject altered ciphertext, altered authenticated headers, and stale rekey epochs.
8. Check block counts across several tree heights and record the actual demo trace.
9. Ensure client and server processes close their sockets and exit cleanly.

## Deliverables and limits

Expected files: `secure_group.py`, `run_demo.py`, `test_secure_group.py`, `requirements.txt`, `README.md`, `DESIGN_REPORT.md`, and `execution_trace.log`, alongside the summary.

This is a localhost simulator with pre-provisioned credentials and in-memory state. Production certificate enrollment, persistent key storage, distributed controller failover, and recovery after missed epochs are outside this assignment. Shared group encryption authenticates membership, not a unique sender; server-side ABAC controls publication through the trusted server.
