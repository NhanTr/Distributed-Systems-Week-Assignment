# Day 7 Lab Design Report

## Objective and requirements

Implement the Client-Centric Consistency Session Proxy Simulator specified on PDF pages 42-43. The proxy must track `V_read` and `V_write`, enforce Monotonic Reads and Read-Your-Writes, expose divergence among S1/S2/S3, and stall/fetch missing write sets when a client selects a stale replica.

## Architecture

```text
Client ID + operation + destination
                |
                v
        SessionProxy
        V_read, V_write per client
                |
      validate / fetch dependencies
                |
        +-------+-------+
        |       |       |
        S1      S2      S3
        |       |       |
        +-- delayed operation delivery --+
```

`Cluster` owns three independent `Replica` objects and a priority queue of replication messages. `SessionProxy` owns client contexts. A replica owns its materialized key/value map, applied write history, dependency buffer, and applied vector clock. The demo and unit tests act as client applications.

## Vector meaning and invariants

The three vector components correspond to origins S1, S2, S3. Component `i = k` means all writes numbered 1 through k from origin i have been applied. Writes have a unique identity `(origin, sequence)`, key, string value, and vector timestamp.

The proxy's `V_read` accumulates contexts observed by successful reads. `V_write` accumulates contexts of acknowledged writes. Neither context decreases. Different client IDs have independent contexts.

Important invariants:

1. A replica advances its clock only after applying complete origin prefixes and causal dependencies.
2. Duplicate deliveries are idempotent.
3. A read is served only when the replica dominates both client vectors, component by component.
4. Failed reads do not advance the client's read context.
5. A session write is acknowledged only after local application, then its context is merged into `V_write`.

## Algorithms

### Read validation and repair

```text
required = componentwise_max(V_read, V_write)
if target.clock does not dominate required:
    log that the request is stalled
    inspect histories of reachable peers
    fetch missing origin prefixes up to required
    apply writes whose dependencies are satisfied; buffer the rest
    if target.clock still does not dominate required:
        raise UnavailableError without returning a value
return target value and merge target.clock into V_read
```

A missing component may be satisfied by any peer retaining the corresponding applied write, including a backup when the origin is offline. The proxy fetches actual operations; it never advances a vector by copying numbers alone. If only part of the required history is reachable, that part may be installed, but the read remains blocked until the full required context is available.

Monotonic Reads follows because each successful read includes the preceding read context. Read-Your-Writes follows because every successful read includes all previously acknowledged session writes. A concurrent later overwrite can supersede a client's value; the guarantee requires inclusion of the write's effects in history, not keeping that value forever.

### Writes and dependency-safe application

Before a session write, the target is synchronized with the client's current context. It increments its own origin component, constructs an immutable write, applies it locally, and schedules deliveries to the other two stores. This extra history preservation also maintains dependencies when a client changes write destinations.

For a write from origin i with sequence k, application requires:

```text
local[i] + 1 == k
local[j] >= write.vector[j] for every j != i
```

Otherwise the write is buffered. Applying a predecessor triggers another attempt to drain the buffer. Consequently, receiving write 2 before write 1 cannot falsely move a replica to version 2.

### Concurrent values

For concurrent writes to the same key, materialization chooses the maximum rank `(sum(vector), origin_index, sequence)`. The same set of writes therefore produces the same value regardless of delivery order. A causally later write has a strictly greater vector sum and supersedes its predecessor. This is an explicit deterministic conflict policy for the simulator; vector clocks themselves do not resolve concurrent values, and this policy is not wall-clock last-write-wins.

All writes remain in history even when their values are superseded. This supports dependency fetching and prevents clock entries from referring to discarded operations.

### Delay and unavailable nodes

A write queues two deliveries due at `now + replication_delay`. `advance(ticks)` processes due deliveries. An offline recipient is retried one tick later; an already queued message can arrive even if its origin has since become unavailable. This represents data already in transit.

On-demand fetching happens synchronously within the request at the current logical time. The logged validation failure and fetch represent stalling until repair completes. Unreachable required history produces an exception so the simulator does not wait indefinitely. Returning a stale value in that situation would violate the contract.

## Evaluation rubric mapping

| Rubric | Weight | Evidence |
| --- | ---: | --- |
| Session vector tracking and validation | 40% | Separate client contexts, componentwise comparison, read/write context merging, tests for incomparable vectors and session isolation |
| Monotonic Reads and Read-Your-Writes | 30% | Independent observer test, Alice profile test, union-context repair test, first two demo scenarios |
| Stale replica fetching and recovery | 20% | Operation-history fetch, delayed delivery, backup source, unavailable history and retry tests, dependency buffer |
| Documentation and design report | 10% | This report, README, lecture summary, generated execution trace |

## Verification and limits

The behavioral test suite exercises the real simulator objects without mocked stores. It covers delayed propagation, out-of-order and duplicate deliveries, convergence under concurrent updates, backup fetching, offline targets, unavailable dependencies, retry after recovery, validation, and independent sessions.

The simulator is single-threaded and assumes trusted, fixed membership. It has no disk persistence, transport protocol, authentication, deletion operation, automatic proxy retry loop, or history compaction. Offline/online transitions represent temporary reachability loss, not reconstruction after data loss. It does not implement strict or sequential consistency, global transactions, or a general-purpose distributed database. The in-memory scheduler and direct method calls are adequate to the lab's requested simulation and keep the results reproducible.
