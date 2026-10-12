# Day 5 Two-Phase Commit Lab Design

## Purpose

Build a readable, runnable Python implementation of the Day 5 practical lab. The result must demonstrate a complete Two-Phase Commit (2PC) transaction across one coordinator socket server and three participant socket servers, durable write-ahead logging, an injected abort, and participant recovery after restart.

The submission is intended as both a working lab solution and study material. A student should be able to run the demonstrations locally, inspect the execution trace, and connect each implementation choice to the lecture concepts.

## Scope and Success Criteria

The solution will:

- Use only the Python standard library.
- Run one coordinator TCP server and three participant TCP servers on loopback addresses.
- Exchange newline-delimited JSON messages over TCP.
- Execute both a successful commit and an injected participant-abort scenario.
- Persist every protocol state transition to a node-specific WAL before sending the message enabled by that transition.
- Flush and `fsync` every WAL append so acknowledged state survives process failure.
- Reconstruct participant state from the WAL after a simulated restart.
- Include deterministic automated tests for protocol behavior, WAL ordering, and recovery.
- Include a design report, usage instructions, a representative trace, and a lecture summary.

The lab will model crash-recovery and vote-abort behavior. It will not attempt Byzantine behavior, network partition resolution, distributed deadlock detection, or production-grade authentication.

## Architecture

### Protocol module

The protocol module owns message constants, node states, JSON-line encoding and decoding, and validation. Keeping wire-format details separate prevents networking code from duplicating protocol rules.

Messages contain a `type`, a transaction ID, and optional metadata. The supported types are:

- `EXECUTE`: client asks the coordinator to begin a transaction.
- `VOTE_REQUEST`: coordinator begins phase 1.
- `VOTE_COMMIT` and `VOTE_ABORT`: participant votes.
- `GLOBAL_COMMIT` and `GLOBAL_ABORT`: coordinator announces phase 2.
- `ACK`: participant confirms the final decision.

### Write-ahead log

Each node owns one append-only text WAL. A record contains a timestamp, node ID, transaction ID, state, and optional details encoded as one JSON object per line.

Appending a record performs write, flush, and `os.fsync` before returning. The coordinator and participants therefore log a state before sending any network message that depends on it. Recovery scans valid records in order and uses the last state for each transaction. A malformed trailing record, representing a torn final append, is ignored; malformed records in the middle are reported as corruption.

### Participant server

Each participant is a threaded TCP server with one `Participant` state machine:

- On `VOTE_REQUEST`, it applies the configured failure policy.
- A normal participant logs `VOTE_COMMIT`, enters `READY`, and replies `VOTE_COMMIT`.
- An injected lock conflict logs `VOTE_ABORT`, enters `ABORT`, and replies `VOTE_ABORT`.
- On `GLOBAL_COMMIT`, a ready participant logs `GLOBAL_COMMIT`, applies its staged work, and replies `ACK`.
- On `GLOBAL_ABORT`, it logs `GLOBAL_ABORT`, rolls back staged work, and replies `ACK`.

On startup, it replays its WAL. Transactions ending in `GLOBAL_COMMIT` or `GLOBAL_ABORT` recover to that final state. A transaction whose last record is `VOTE_COMMIT` recovers to `READY`, reflecting the blocking state in which the participant must learn the coordinator's decision.

### Coordinator server

The coordinator accepts `EXECUTE`, logs `START_2PC`, and contacts all configured participants:

1. It sends `VOTE_REQUEST` to each participant and collects votes.
2. If every response is `VOTE_COMMIT`, it logs `GLOBAL_COMMIT`; otherwise, including connection errors and timeouts, it logs `GLOBAL_ABORT`.
3. It broadcasts the durable global decision to all reachable participants and collects acknowledgements.
4. It returns the decision, votes, and acknowledgements to the client.

Participant calls may run concurrently, but the decision is deterministic: any abort, invalid reply, connection failure, or timeout forces a global abort. If a participant is unreachable during phase 2, the coordinator retains the durable decision in its WAL and reports the missing acknowledgement; the demo can safely retry decision delivery.

### Demo runner

A single command starts all four servers in background threads, waits until their ports are ready, submits transactions, prints protocol events, and shuts the servers down cleanly.

The demo uses temporary or explicitly configured WAL directories and runs three scenarios:

1. All participants vote commit, producing `GLOBAL_COMMIT`.
2. P3 injects a lock conflict, producing `GLOBAL_ABORT` and rollback at P1/P2.
3. A participant is stopped and reconstructed from the same WAL, demonstrating recovery of its final transaction states.

## Data and Control Flow

For each transaction, the invariant is that a decision-causing state is durable before its corresponding outbound message:

```text
Client -> Coordinator: EXECUTE
Coordinator: fsync START_2PC
Coordinator -> Participants: VOTE_REQUEST
Participants: fsync VOTE_COMMIT or VOTE_ABORT
Participants -> Coordinator: vote
Coordinator: fsync GLOBAL_COMMIT or GLOBAL_ABORT
Coordinator -> Participants: global decision
Participants: fsync global decision, apply or roll back
Participants -> Coordinator: ACK
Coordinator -> Client: result
```

Each server handles one JSON request per connection and returns one JSON response, keeping framing and failure behavior easy to reason about.

## Failure Injection and Recovery

Failure injection is explicit and transaction-scoped. The demo configures P3 to reject one chosen transaction as a simulated lock conflict rather than relying on timing or randomness.

The restart demonstration creates a fresh participant object from the same WAL path. It does not copy in-memory state. Tests assert that the reconstructed object reports the same final states recorded before shutdown.

Network failures and malformed messages produce structured errors and never lead to commit. A timeout or invalid phase-1 response is treated as `VOTE_ABORT`. Server-side exceptions are logged to the trace while preserving process availability for later requests.

## Repository Deliverables

The Day 5 directory will contain:

- `two_phase_commit.py`: protocol, WAL, coordinator, participant, and TCP server implementation.
- `run_demo.py`: executable three-scenario demonstration.
- `test_two_phase_commit.py`: deterministic unit and integration tests.
- `README.md`: architecture, commands, protocol explanation, and rubric mapping.
- `SUMMARY_AND_LAB_SOLUTION.md`: lecture knowledge summary and lab solution discussion.
- `execution_trace.log`: captured representative demo output.

Runtime-generated WAL files will live under a gitignored `wal/` directory and will not be committed.

## Testing Strategy

Development will follow test-driven development. Tests will use temporary directories and operating-system-assigned ports to avoid collisions.

Coverage will include:

- WAL append and recovery, including a torn trailing line.
- Participant commit vote and injected abort vote.
- Participant final commit and rollback transitions.
- Coordinator commits only when all three participants vote commit.
- One abort vote forces global abort for every reachable participant.
- Timeout, unavailable node, malformed response, and duplicate decision handling.
- Recovery of `READY`, `COMMIT`, and `ABORT` states from WAL.
- End-to-end TCP execution with one coordinator and three participants.

The final verification will run the full test suite, run the demo from a clean temporary WAL location, compare its trace with the lecture-required sequence, and confirm only intended repository files changed.

## Constraints and Trade-offs

Threaded loopback servers satisfy the required socket architecture while keeping startup and teardown simple for a classroom lab. Separate operating-system processes would provide stronger failure isolation but add orchestration complexity that does not improve the assessed 2PC concepts.

Synchronous `fsync` intentionally reduces throughput because stable storage accounts for 30% of the rubric. JSON-line WALs and messages favor inspectability over compactness. The simulator demonstrates 2PC's safety-oriented decision rules but does not claim to solve its blocking behavior.
