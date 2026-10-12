# Day 5 Lab: Durable Two-Phase Commit Simulator

This lab implements the lecture's Two-Phase Commit (2PC) exercise using one coordinator TCP server and three participant TCP servers. It demonstrates a successful commit, a participant lock-conflict abort, synchronous write-ahead logging (WAL), and participant state recovery after restart.

## Requirements and commands

- Python 3.10 or newer
- No third-party packages
- Permission to bind loopback (`127.0.0.1`) ports

Run from the `Day 5` directory:

```bash
python3 -m unittest -v
python3 run_demo.py --wal-dir wal --output execution_trace.log
```

The second command prints the trace, writes the same content to `execution_trace.log`, and creates runtime WALs under the ignored `wal/` directory. Every demo run removes only the four known runtime WAL files first so its results remain deterministic.

## Files

- `two_phase_commit.py`: protocol validation, durable WAL, participant and coordinator state machines, JSON-line RPC, and threaded TCP server lifecycle.
- `run_demo.py`: starts all four servers and runs the commit, abort, and restart scenarios.
- `test_two_phase_commit.py`: unit and end-to-end tests using temporary directories and operating-system-assigned ports.
- `execution_trace.log`: trace produced by the verified demo.
- `SUMMARY_AND_LAB_SOLUTION.md`: lecture knowledge summary and explanation of the solution.
- `.gitignore`: excludes runtime WALs and Python cache files.

## Architecture and protocol

Every connection carries one newline-delimited JSON request and one JSON response. Messages use a non-empty string `tx_id` and one of these types:

| Sender | Message | Meaning |
| --- | --- | --- |
| Client | `EXECUTE` | Ask the coordinator to execute a transaction. |
| Coordinator | `VOTE_REQUEST` | Begin phase 1 and ask whether a participant is ready. |
| Participant | `VOTE_COMMIT` | The participant durably entered `READY`. |
| Participant | `VOTE_ABORT` | The participant cannot commit. |
| Coordinator | `GLOBAL_COMMIT` | Every participant voted commit; apply the work. |
| Coordinator | `GLOBAL_ABORT` | At least one vote failed; roll back the work. |
| Participant | `ACK` | The final decision was durably recorded and applied. |

The coordinator contacts participants concurrently during each phase. A timeout, refused connection, invalid JSON response, unknown vote, or explicit `VOTE_ABORT` prevents commit. The coordinator logs the final decision before broadcasting it.

### State transitions

Participant state transitions are:

```text
No state --VOTE_REQUEST/accept--> READY
No state --VOTE_REQUEST/conflict--> ABORT
READY --GLOBAL_COMMIT--> COMMIT
READY --GLOBAL_ABORT--> ABORT
ABORT after VOTE_ABORT --GLOBAL_ABORT--> ABORT (finalized)
```

Repeated vote requests and identical final decisions are idempotent. A contradictory final decision returns `ERROR` without changing durable state. After a restart, the last `VOTE_COMMIT` record recovers as `READY`; `GLOBAL_COMMIT` and `GLOBAL_ABORT` recover as final `COMMIT` and `ABORT` states.

Coordinator state flow is:

```text
fsync START_2PC
  -> multicast VOTE_REQUEST
  -> collect votes
  -> fsync GLOBAL_COMMIT if every vote commits, otherwise GLOBAL_ABORT
  -> multicast durable decision
  -> collect ACK responses
```

Reusing an already decided transaction ID returns the recovered durable decision without starting a second voting round.

## Stable write-ahead log

Each node writes one compact JSON object per line. A representative record is:

```json
{"details":{},"node_id":"P1","state":"VOTE_COMMIT","timestamp":"2026-10-12T00:00:00+00:00","tx_id":"101"}
```

`WriteAheadLog.append` writes the record, flushes the Python stream, and calls `os.fsync` before the protocol sends the corresponding message. Recovery replays records in order and keeps the last state for each transaction. A truncated final line is treated as a torn append and ignored; corruption before the last line raises `WALCorruptionError`.

## Failure injection and recovery

P3 is configured to reject transaction `102`. Its `VOTE_ABORT` forces `GLOBAL_ABORT`, so P1 and P2 roll back even though both voted commit. After both demo transactions, the P3 server stops and a new `Participant` object is constructed solely from `P3.wal`. It recovers transaction `101` as `COMMIT` and transaction `102` as `ABORT`.

2PC preserves atomic decision rules, but it can block. A participant that has durably voted commit is in `READY` and cannot decide safely by itself while the coordinator's decision is unavailable. This simulator exposes that recoverable `READY` state; it does not pretend to solve blocking, partitions, or Byzantine failures. Production systems commonly replicate commit decisions through Raft or Paxos.

## Evaluation rubric mapping

| Rubric item | Weight | Evidence |
| --- | ---: | --- |
| 2PC protocol and state machines | 40% | `Coordinator`, `Participant`, real TCP `JsonLineServer`, commit/abort integration tests, and `execution_trace.log` |
| Stable-storage WAL | 30% | Per-node JSON-lines logs, flush plus `os.fsync`, replay, torn-write handling, and WAL tests |
| Failure injection and log recovery | 20% | P3 lock conflict, timeout/unavailable-node tests, and reconstruction of P3 from its WAL |
| Design report and documentation | 10% | This README, the knowledge summary, execution trace, tests, and the committed design specification |

The tests cover normal commit, forced abort, duplicate messages, conflicting decisions, malformed and oversized messages, invalid votes, unavailable and non-responsive participants, durable decision reuse, torn WAL writes, and restart recovery.
