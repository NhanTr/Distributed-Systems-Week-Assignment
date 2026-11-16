# Secure Group Multicast & ABAC — Design report

## Requirements and implementation

The source assignment is PDF pages 41–45. `secure_group.py` supplies a binary Logical Key Hierarchy (LKH), authenticated AES-256-GCM encryption, TCP membership and publication operations, and default-deny ABAC. `run_demo.py` launches the server plus eight separate clients and proves membership exclusion with actual cryptographic operations. `test_secure_group.py` supplies automated checks. The lecture PDF is unchanged.

The implementation follows the Python/TCP approach in `LAB_DESIGN.md`. One lock serializes all membership changes, event delivery, and publication. Per-client TCP ordering makes rekey events precede payloads for the new epoch, without needing a separate acknowledgement protocol. The client processes in the demo listen while idle; `Client.request()` also drains ordered events while awaiting a reply.

## Tree and key ownership

Nodes use binary heap numbering: root 1, children `2n` and `2n+1`, leaves 8–15 for capacity 8. This numbering differs from the lecture's root label `K_0`; the logical tree is the same. A client at leaf 15 stores only nodes **15, 7, 3, 1**. A leaf-8 client stores **8, 4, 2, 1**. No sibling subtree keys are delivered.

Capacity must be a power of two of at least 2. The controller rejects duplicate identities, inactive leaves, and full-capacity joins before changing the epoch. It generates independent 32-byte keys with `secrets.token_bytes`, including a new leaf key on reuse. Each key has a version equal to the epoch when it was generated. Membership epochs increase monotonically.

The individual pre-provisioned authentication/bootstrap secret is separate from the four tree keys. Thus a height-3 client holds four tree keys plus its enrollment secret and session key. The lecture's `h+1` count refers to the tree path; it does not count these transport credentials.

## Join

1. Verify the identity using its pre-provisioned secret, and ensure a free leaf exists.
2. Increment the epoch and replace the entrant's leaf and all ancestor keys.
3. For each changed ancestor already serving existing members, wrap the replacement under that node's previous key. Existing clients know the applicable old keys and update only nodes on their own paths. There are at most `h` such blocks.
4. Send the existing members an ordered JOIN event. Deliver the entrant's new path separately under its individual secret inside its authenticated session. The bootstrap includes no previous ancestor key.

An entrant's new root key fails AES-GCM authentication against a recorded old payload. The demo checks this directly, beyond the normal client's epoch guard.

## Leave

1. Remove the member from the active registry and increment the epoch.
2. Replace its leaf and each ancestor, processing bottom-up.
3. At each internal node, wrap its replacement under each occupied child's current key. The child on the affected path already has its replacement key. An unaffected sibling keeps its current key.
4. Broadcast one LEAVE event containing these blocks to each survivor. No rekey event or future payload is sent to the departed session.

For member 7 at leaf 15 leaving a full capacity-8 tree, the five blocks are:

| Updated node | Protecting child | Reason |
| --- | --- | --- |
| 7 | 14 | Only surviving leaf in this pair |
| 3 | 6 | Unaffected sibling subtree |
| 3 | 7 | Newly rekeyed subtree |
| 1 | 2 | Unaffected half of tree |
| 1 | 3 | Newly rekeyed half |

The departed client knows no surviving child wrapper for the first replacement and cannot follow the chain to any later replacement. `recover()` explicitly tries every available block against the member's accumulated knowledge. The leave test retains every historical path-key version the departing client learned during earlier joins, and verifies that it cannot recover the new root; a survivor recovers that root with the same procedure. The demo retains its departing client's full current path, verifies that it recovers no replacements, and attempts future payload decryption directly with its old root key, requiring `InvalidTag`.

The lab's labels for forward/backward secrecy differ from common terminology. This report uses the precise properties: entrants cannot decrypt past traffic; departed members cannot decrypt future traffic. These are membership properties, not ephemeral-session perfect forward secrecy.

## Encryption and authenticated transport

AES-256-GCM uses fresh random 96-bit nonces. Application authenticated metadata identifies payload type, epoch, root node and key version. Rekey metadata identifies JOIN/LEAVE, epoch, updated node, wrapper node and wrapper version. Altering ciphertext or this metadata invalidates the authentication tag. Clients apply rekeys to a temporary candidate map and commit only after recovering the epoch's root, preserving state on an authentication failure. Stale or skipped epochs fail closed.

The server sends a random 256-bit challenge. A client returns HMAC-SHA256 over a canonical transcript binding identity, challenge and a fresh client nonce. A separate HMAC label derives the session key from that transcript and the individual secret. Successful bootstrap delivery also authenticates the server to the client. Subsequent requests and events are AEAD-protected with direction and strictly increasing sequence numbers as metadata. This binds sensitive requests to the authenticated identity and rejects replay or direction reflection. No secret is transmitted in cleartext.

Frames are a 4-byte unsigned network-order length and a JSON object, bounded at 1 MiB. Reads handle partial TCP delivery. Published text is limited to 4 KiB. The service binds only to `127.0.0.1`, uses timeouts, and rejects malformed records by closing their session. Disconnecting an active session triggers leave/rekey. On failed broadcasts and shutdown, `shutdown()` wakes blocked readers before socket closure, and the server joins its handler threads.

Each multicast application envelope is encrypted **once** under the root key. Identical envelopes are then placed in separate authenticated transport records for connected members. Those additional session encryptions do not constitute repeated application encryption. This emulates multicast over ordered TCP streams; it is not IP multicast.

## Policy

Publication is allowed only when all conditions hold:

- The authenticated session is the identity's active group session.
- The server-owned role equals `Publisher`.
- The action equals `publish`.
- Server-local time is between 08:00:00 and 18:00:00, inclusive.
- The payload is a string within the service's size bound.

Unknown actions, malformed attributes, Subscribers, inactive sessions and out-of-hours requests are denied. The clock is read by the server; request `time` and `role` fields are ignored. Normal operation uses local wall-clock time. The demo uses an explicit noon override for repeatability; tests additionally start a server fixed at 20:00 and demonstrate that a client claiming noon remains denied. Unit tests check both exact permitted boundaries and the immediately excluded neighboring times.

## Complexity and evidence

For tree capacity `C=2^h`, the path holds `h+1` tree keys, JOIN uses at most `h` encrypted update blocks (plus the entrant's separate bootstrap), and a departure from a full tree uses `1+2(h-1)=2h-1` blocks. Sparse subtrees can reduce this count. Tests check full-tree capacities 2, 4, 8, 16 and 32, expecting respectively 1, 3, 5, 7 and 9 leave blocks.

The lecture's `2h` estimate is an upper bound: its sample six blocks at height 3 becomes five here because there is no block to the departed leaf. The blocks are bundled into one event delivered seven times in the capacity-8 leave example. Block count is `O(log C)`; TCP deliveries and total bytes across recipients still grow with active membership. This simple implementation scans membership to determine occupied subtrees and preallocates `2C-1` node keys; it does not claim logarithmic total CPU time. A production occupancy index could improve that cost.

Reproduce validation with the commands in `README.md`. `execution_trace.log` is captured output from an actual demo, including process IDs, exclusion checks, five leave blocks, boundary checks, integrity checks, scaling and normal process exits. The automated suite runs this demo in a subprocess and requires success within 30 seconds without forced shutdown; it also exercises authentication failure, duplicate joins, membership reuse, full capacity, session replay, oversized frames, and policy enforcement over real TCP.

## Limits

This is an educational localhost service, with an in-memory trusted controller and provisioned credentials. It provides no certificate enrollment, persistent key storage, controller failover, or recovery after a missed epoch. The launcher supplies the full registry only to the server and each client only its own secret. For exclusion proofs, the trusted demo launcher receives selected client snapshots; these keys are never printed or distributed to another client.

Slow clients can delay work while broadcasts hold the ordering lock; this is suitable for the lab, not a high-volume deployment. Accepted connections are not globally capped and completed handler thread objects are retained until shutdown; long-running or hostile connection churn would need resource controls. The demo's process-control pipes are local test orchestration; membership, key updates and payloads travel over TCP. Group-key encryption authenticates possession of the shared key, not a unique sender. Server-side ABAC governs publication through the trusted service. Revocation cannot erase plaintext already received or protect against collusion with a remaining authorized member.
