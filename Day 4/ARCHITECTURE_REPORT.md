# Architectural Design Report: Bully Leader Election Simulator

## Page 1 of 2 - Architecture and protocol

### Objective

The simulator models five distributed processes, `P1` through `P5`, and implements
the Bully leader-election algorithm. Each process is represented by a Python thread,
but communication never uses shared queues or direct method calls. Every protocol
message is serialized as newline-delimited JSON and sent over a TCP socket bound to
`127.0.0.1`. The initial coordinator is `P5`. The experiment terminates `P5`, waits
for heartbeat failure detection, and verifies that all surviving processes converge
on `P4`, the active process with the highest ID.

### Components

- `BullyCluster` creates five listening sockets on ephemeral ports, constructs the
  process threads, injects the `P5` crash, and checks the final leader views.
- `ProcessNode` owns one listener socket, leader state, heartbeat timestamp, and
  election state. Its service loop accepts messages and checks local timers.
- `TraceLog` serializes concurrent log writes so the output remains readable.
- `SimulationResult` returns the final leader view and complete execution trace to
  the CLI and integration tests.

```text
        TCP/JSON       TCP/JSON       TCP/JSON       TCP/JSON
  P1 <----------> P2 <----------> P3 <----------> P4 <----------> P5
   |               |               |               |               |
 thread          thread          thread          thread          thread
 listener        listener        listener        listener        listener
```

The physical transport is full-mesh rather than a ring: each process knows the
address and priority ID of every peer, matching the global-knowledge assumption of
the Bully algorithm. Ephemeral ports avoid collisions with fixed services and make
the test repeatable on a developer machine or CI worker.

### Message protocol

- `HEARTBEAT`: sent periodically by the current coordinator to refresh followers'
  failure timers.
- `ELECTION`: sent by an initiator to every process with a larger ID.
- `OK`: returned by a higher process, forcing the lower initiator to step aside.
- `COORDINATOR`: broadcast by the winner so all lower processes adopt it.

`P2` has the shortest configured failure timeout, making the demonstration trace
match the lecture example. When `P2` contacts `P3` and `P4`, they reply `OK` and
start their own elections. `P4` contacts `P5`, receives no response, and becomes
the coordinator. The staggered timers are only for deterministic demonstration;
the protocol remains correct if another surviving node detects the failure first.

<div style="page-break-after: always;"></div>

## Page 2 of 2 - Failure handling, correctness, and verification

### Failure model and concurrency control

The lab uses a crash-stop failure model. Terminating `P5` sets its stop event and
closes its listening socket, so subsequent connection attempts fail or time out.
There is no Byzantine behavior and no network partition simulation. A process uses
an `election_lock` and `election_running` flag to prevent duplicate local elections
when timeout detection and incoming `ELECTION` messages occur close together.

The listener loop stays responsive while election work runs in a short-lived helper
thread. This separation is important: a process must be able to return `OK` while
its own election is waiting for replies from higher IDs. Socket operations have
finite timeouts, and a lower initiator waits for a bounded coordinator announcement.
If that announcement never arrives, it restarts the election.

### Correctness argument

**Safety.** A process declares itself leader only when no process with a higher ID
answers its `ELECTION` messages. In the demonstrated active set `{P1, P2, P3, P4}`,
only `P4` satisfies this condition. Every `COORDINATOR` receiver atomically replaces
its leader view with `P4`.

**Liveness.** After the crashed leader stops sending heartbeats, at least one follower
crosses its local timeout and starts an election. Each socket request and coordinator
wait is bounded, so the election cannot block forever on `P5`. The maximum active ID
eventually receives no higher reply and broadcasts victory.

**Priority rule.** Lower processes stop competing after an `OK` response and wait for
the higher process's announcement. Thus an active process with a greater ID always
supersedes a lower candidate, which is the defining Bully behavior.

### Complexity and limitations

In the best case, the second-highest active process starts the election, producing
linear message cost. In the worst case, the lowest process starts and triggers a
cascade, producing `O(N^2)` election traffic. Heartbeats add ongoing `O(N)` traffic
per interval. Like the lecture algorithm, this simulator assumes unique IDs, known
peer addresses, sufficiently reliable TCP delivery, and timeout-based failure
detection. Delays can cause false suspicions; production systems generally use
consensus protocols and stronger membership management.

### Verification

`test_bully_election_simulator.py` runs the complete socket-based failure scenario.
It asserts that `P1` through `P4` all report leader `P4`, and that the trace contains
leader termination, automated timeout detection, higher-ID election messages, the
new-leader decision, and the `COORDINATOR` broadcast. The CLI can reproduce the
submitted trace with:

```bash
python3 bully_election_simulator.py --output execution_trace.log
python3 -m unittest -v
```
