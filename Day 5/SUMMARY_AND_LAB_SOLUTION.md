# Lecture 05: Transactions, Recovery, and Fault Tolerance

## Knowledge summary

### Distributed transactions and ACID

A transaction groups reads, writes, and state changes into one logical unit. `BEGIN_TRANSACTION` opens the unit; `END_TRANSACTION` requests commit; `ABORT_TRANSACTION` rolls it back. A distributed transaction extends that unit across independent resource managers connected by a network.

ACID describes its required behavior:

- **Atomicity:** all operations commit or all are undone.
- **Consistency:** committed execution preserves application, schema, and integrity invariants.
- **Isolation:** concurrent transactions behave as though executed serially; uncommitted writes are not exposed.
- **Durability:** committed effects survive later crashes.

A flat transaction has one begin/end scope, so any failed step can roll back the whole operation. A nested transaction is a tree: a child may abort and be retried without immediately aborting its parent. A child's local commit is provisional and visible within the hierarchy; it becomes permanent only when the top-level parent commits.

### Shadow workspaces, WAL, and transaction middleware

A shadow workspace updates private copies and atomically replaces master state at commit. It gives clear isolation but copying large pages is expensive. Write-ahead logging (WAL) appends an intention or state record to stable storage before changing data or sending a message based on that state. Production databases generally prefer WAL because sequential appends are cheaper than full shadow copies.

In the X/Open Distributed Transaction Processing model, the Application Program defines transaction boundaries, the Transaction Manager assigns global XIDs and coordinates commit/recovery, and Resource Managers own databases or queues. The XA interface connects the manager and resources through calls such as `xa_start`, `xa_end`, `xa_prepare`, `xa_commit`, and `xa_rollback`. A Transaction Processing monitor supplies this coordination across heterogeneous systems.

### Concurrency control

Strict 2PL uses shared locks for reads and exclusive locks for writes. A transaction acquires locks during its growing phase and releases all of them only after commit or abort. Holding locks through the final decision prevents dirty reads and cascading aborts, but distributed wait-for dependencies can create global deadlocks.

Timestamp ordering gives each transaction a logical timestamp and checks it against each item's maximum read and write timestamps. An operation that violates the order aborts and restarts. Optimistic Concurrency Control (OCC) assumes conflicts are rare: transactions read into private workspaces, validate before commit, and either publish their writes or abort. OCC suits read-heavy workloads but repeated conflicts cause wasted work.

### Dependability and failure models

Availability is the probability that a service is usable at an instant:

```text
A = MTTF / (MTTF + MTTR)
```

Reliability measures uninterrupted correct service across a time interval. A system may have high availability but low reliability if it fails briefly and often. Safety asks whether failures avoid catastrophic outcomes; maintainability reflects how quickly repair can restore service, commonly represented by MTTR.

The causal chain is **fault -> error -> failure**. A fault is the underlying cause, an error is the resulting invalid internal state, and a failure is externally visible deviation from the specification. Faults may be transient, intermittent, or permanent.

Failure models increase in severity:

- Crash-stop: a node halts permanently and produces no more output.
- Crash-recovery: it reboots and reconstructs state from stable logs.
- Omission: requests or responses are dropped at the receiver, sender, or channel.
- Timing: a correct response arrives outside required time bounds.
- Byzantine: a node sends arbitrary, corrupted, or contradictory information.

Redundancy masks failures. Information redundancy adds error-correcting bits, temporal redundancy retries work, and physical redundancy adds replicas. Triple Modular Redundancy (TMR) runs three units and majority-votes their output. With independent component reliability `p`, its reliability is `3p^2 - 2p^3`, improving on one component when `p > 0.5`.

To tolerate `k` crash-stop failures, at least `k + 1` replicas leave one survivor. Simple majority masking against up to `k` bad votes needs `2k + 1` replicas. Byzantine agreement with contradictory messages requires at least `3k + 1` processes under the classic unauthenticated model; tolerating one Byzantine process therefore requires four.

### Consensus and reliable RPC

Consensus requires agreement, validity, and termination. The FLP impossibility result says a deterministic algorithm in a fully asynchronous system cannot guarantee both safety and liveness when even one process may crash. Practical systems add partial synchrony and timeouts or use quorum protocols such as Paxos and Raft.

RPC can lose a request, crash during server processing, lose a response, or lose the client. If the client disappears while its server-side computation continues, that computation is an orphan that may consume CPU, memory, and locks. Orphans can be removed through extermination using call logs, reincarnation using reboot epochs, gentle reincarnation that checks for a live owner, or lease expiration. Lease correctness depends on bounded clock drift.

### Two-Phase and Three-Phase Commit

2PC has one coordinator and multiple participants:

1. **Voting:** the coordinator durably records `START_2PC` and sends `VOTE_REQUEST`. Each participant durably records `VOTE_COMMIT` or `VOTE_ABORT` before replying.
2. **Decision:** all commit votes produce a durable `GLOBAL_COMMIT`; any abort, timeout, or failure produces `GLOBAL_ABORT`. The coordinator broadcasts the decision, and participants log it before acknowledging.

A participant that voted commit is in `READY`. It cannot safely commit or abort without learning the global decision, so 2PC is blocking if the coordinator becomes unavailable at that point. The lecture's severe scenario combines a coordinator crash before decision delivery with a participant failure, leaving survivors unable to infer whether a decision exists.

3PC inserts a `PREPREPARE`/pre-commit phase between `READY` and final commit. Under bounded fail-stop assumptions, a new coordinator can use whether any participant reached pre-commit to choose commit or abort without blocking. Under a network partition, however, separate groups may make conflicting progress; 3PC is therefore unsafe for general asynchronous partitions.

Modern cloud systems replicate decision logs with Paxos or Raft. A majority quorum stores each proposal, and leader election replaces a failed coordinator. This does not remove all transaction-coordination costs, but it avoids one unreplicated decision-maker.

### Recovery, checkpoints, and message logging

Backward recovery restores an earlier valid checkpoint, discarding later work. Forward recovery transforms an erroneous state into a new valid state using application-specific knowledge.

Independent checkpoints are easy to take but may be mutually inconsistent. If one rollback invalidates a recorded message dependency, another process must roll back before that message; the chain can continue to system startup as the **Domino Effect**. A recovery line is the latest set of local checkpoints forming a consistent global state with no recorded receive lacking its corresponding send.

The Chandy-Lamport snapshot algorithm captures a consistent global state without stopping computation. An initiator saves its local state and sends markers. A process receiving its first marker saves local state, records that incoming channel as empty, and forwards markers. Until a later marker arrives on another incoming channel, messages received there are recorded as that channel's in-flight state.

Message logging assumes piecewise deterministic execution: replaying the same nondeterministic message-delivery events from a checkpoint recreates the same state. In pessimistic logging, every message is synchronously persisted before delivery, preventing orphans and simplifying recovery at the cost of runtime latency. In optimistic logging, messages are buffered and flushed asynchronously for throughput, but a crash can lose determinants and require dependency tracking plus rollback of orphan processes.

## Lab solution

The implementation uses Python's standard library and real loopback TCP sockets. `JsonLineServer` runs the coordinator and each of P1, P2, and P3. `WriteAheadLog` appends JSON-line state records and calls both `flush` and `os.fsync` before a state-dependent response or broadcast. The coordinator contacts all participants concurrently but commits only when every normalized vote is `VOTE_COMMIT`.

Three demonstrations satisfy the lab:

1. Transaction `101`: P1, P2, and P3 vote commit; the coordinator records and broadcasts `GLOBAL_COMMIT`; every participant reaches `COMMIT`.
2. Transaction `102`: P3 injects a lock conflict and records `VOTE_ABORT`; the coordinator records `GLOBAL_ABORT`; P1 and P2 roll back their prepared work.
3. P3 restart: the server stops and a new participant instance reads only `P3.wal`, recovering `101=COMMIT` and `102=ABORT`.

The tests also cover unavailable and slow participants, invalid replies, malformed and oversized JSON, duplicate requests and decisions, contradictory decisions, coordinator decision reuse, corrupt WAL records, and a torn final WAL append.

Run from `Day 5`:

```bash
python3 -m unittest -v
python3 run_demo.py --wal-dir wal --output execution_trace.log
```

The generated trace follows the lecture's expected order: `START_2PC`, vote collection, durable global decision, decision broadcast, participant acknowledgement, and final system outcome.
