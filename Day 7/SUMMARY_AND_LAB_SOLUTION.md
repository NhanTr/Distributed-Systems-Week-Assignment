# Lecture 07: Consistency Models in Distributed Systems

Source: `Lect07_consistency_models.pdf`, 46 PDF pages. The assignment is on PDF pages 42-43 (slides 37-38). Page numbers below refer to PDF pages.

## 1. Replication and consistency foundations (pages 4-6)

Replication improves fault tolerance, read capacity, and geographic access latency. The challenge is that writing one copy temporarily creates different states across replicas. Synchronizing all copies requires communication and can delay operations.

A consistency model is a contract defining which operation orderings and read results applications may observe. It does not prescribe a single implementation.

- **CAP:** during a partition, a system cannot guarantee both linearizable consistency and a successful response to every request at every non-failing node.
- **PACELC:** during partitions, consider availability versus consistency; during normal operation, consider latency versus consistency.

The lecture uses Spanner and Dynamo/Cassandra as examples of different priorities. Actual behavior depends on the selected protocol and configuration; these labels are broad teaching categories.

Notation: `Wi(x)a` writes `a` to `x`; `Ri(x)b` reads `x` and returns `b`. Operations progress left to right in each process history. Data starts as `NIL` unless specified otherwise.

## 2. Continuous consistency and conits (pages 7-9)

Continuous consistency bounds how much replicas may disagree instead of choosing only between perfect synchronization and unrestricted temporary divergence.

A **conit**, or consistency unit, is the group of data over which a bound is measured: a stock record, shopping cart, or weather report, for example.

| Dimension | Meaning | Example bound |
| --- | --- | --- |
| Numerical deviation | Difference in values or accumulated update weights | Stock quote differs by less than $0.02 |
| Staleness | Age of missing updates | Replica data is less than 10 seconds behind |
| Ordering deviation | Amount of tentative work whose final order remains unresolved | At most three tentative writes |

Large conits aggregate tracking but can cause **false sharing**: an update to one item triggers synchronization of unrelated items grouped with it. Small conits reduce false sharing but require more metadata. Tracking overhead grows with the number of conits and vector entries; finer granularity does not inherently imply exponential growth, despite the slide's wording.

## 3. Order-based data-centric models (pages 11-21)

| Model | Contract | What it permits |
| --- | --- | --- |
| Strict | A read observes the latest write in absolute physical time. | An idealized instantaneous global visibility model. |
| Sequential | Every operation fits one sequence preserving each process's program order. | Different valid global interleavings, without a wall-clock ordering requirement. |
| Causal | Causally related writes appear in the same order everywhere. | Different orders for independent concurrent writes. |
| FIFO / PRAM | Every writer's writes are observed in that writer's issue order. | Different interleavings between different writers. |

### Strict and sequential consistency

The lecture's strict model assumes an absolute notion of the latest write and is a theoretical baseline. Network delay prevents instantaneous visibility across arbitrary distances. This should be distinguished from **linearizability**, which models an operation as taking effect at some point between its invocation and completion and preserves real-time order of nonoverlapping operations.

For sequential consistency, construct one legal sequence of all reads and writes. Preserve each process's order and ensure each read sees the latest preceding write to that item in the sequence.

With one write of `a` and one write of `b`, two readers both seeing `b` then `a` can fit a shared sequence. One reader seeing `b -> a` while another sees `a -> b` cannot, assuming neither value is rewritten.

### Causality and vector timestamps

Causal dependency comes from program order, observing another write through a read, and transitivity. If P2 reads P1's `a` and then writes `b`, the write of `a` precedes the write of `b`. A causal replica must buffer `b` until it includes `a`.

For vectors of equal length:

```text
A <= B iff A[i] <= B[i] for every component i
A < B iff A <= B and A != B
A || B iff neither A <= B nor B <= A
merge(A, B)[i] = max(A[i], B[i])
```

The final case means concurrency. Ordinary tuple sorting or comparing sums cannot replace componentwise comparison when checking causal or session dependencies.

FIFO is weaker than causal consistency because it does not track dependencies across different writers. A write from P2 can arrive before the write from P1 that P2 read. Independent FIFO channels alone also need application-level handling for reconnects or multiple paths.

The lecture's banking example illustrates noncommuting **operations**: deposit $20 then apply 10% interest gives $132; interest then deposit gives $130. These calculations assume operation replay. Literal assignments `balance = 120` and `balance = 110` would instead finish at the last assigned value. Applications must define which update semantics they use.

## 4. Synchronization-based models (pages 22-27)

**Weak consistency** groups work between explicit synchronization operations. Synchronization accesses are sequentially consistent; earlier writes complete before the required synchronization, and later data accesses wait for preceding synchronization operations.

The intended valid trace is a writer publishing `a`, then `b`, followed by synchronization, and a reader synchronizing before reading `b`. A read made before the relevant synchronization can be stale. Such a read lacks the freshness guarantee; staleness outside a required synchronization boundary is not automatically a violation of the weak model itself.

**Release consistency** separates acquiring access from releasing it. Acquire makes the required prior updates visible; release publishes preceding work. Eager propagation communicates at release; lazy propagation postpones update transfer until another process needs it at acquire. Lazy protocols may still exchange metadata or coordination messages.

**Entry consistency** explicitly associates protected data with synchronization objects. Acquiring a lock synchronizes its associated objects. Exclusive access protects writes, while shared access permits readers. The explicit data-to-lock mapping gives finer granularity and requires programmer discipline.

Weak, release, and entry consistency are synchronization contracts, not simply the last three points on one unconditional strongest-to-weakest list. Their guarantees depend on the synchronization rules applications follow.

## 5. Eventual consistency and client sessions (pages 29-36)

Eventual consistency promises convergence after updates stop, provided communication and conflict resolution eventually succeed. It does not specify a freshness deadline or independently guarantee availability for every possible failure.

A mobile client can move between replicas and see its cart disappear or an older inbox reappear. Client-centric guarantees carry enough session history to prevent specific anomalies:

| Guarantee | Required dependency | Example |
| --- | --- | --- |
| Monotonic Reads | Later reads include history observed by earlier reads. | Refreshing an inbox does not return an older state. |
| Monotonic Writes | Later writes follow earlier writes by the same client. | Deploy the required library before its dependent application. |
| Read-Your-Writes | Reads include the client's acknowledged write history. | A saved profile edit is reflected after switching replicas. |
| Writes-Follow-Reads | Writes carry dependencies on previously observed updates. | A reply is not applied before its parent post. |

These four guarantees address different anomalies; the numbered list is not a strict hierarchy.

The client or proxy maintains `V_read` for observed read history and `V_write` for acknowledged write history. A target server must dominate the relevant context before serving an operation. Otherwise it must obtain missing writes, wait, route elsewhere, or reject the request if it cannot preserve the guarantee.

To enforce both Monotonic Reads and Read-Your-Writes on one read, require:

```text
required = merge(V_read, V_write)
server_clock >= required componentwise
```

Testing only one of the two contexts is insufficient. After a successful read, merge the returned server context into `V_read`; after an acknowledged write, merge its context into `V_write`. Never advance clocks without actually installing the represented updates.

## 6. Case studies and review answers (pages 38-41)

**Dynamo:** the lecture illustrates highly available storage with concurrent version branches. Vector clocks distinguish descendants from concurrent versions; application reconciliation merges conflicts and writes a new descendant. A simple shopping-cart union retains additions but needs additional semantics to handle removals correctly. The slide's absolute 100% availability wording is a design aspiration rather than an unconditional failure guarantee.

**Bayou:** disconnected replicas accept tentative writes. A primary establishes a committed order, while application dependency checks and merge procedures handle conflicts. Replicas eventually reconcile with the committed history.

### Numerical problem 1: vector causality

```text
Op1 = (2, 0, 1)
Op2 = (3, 1, 1)
Op3 = (2, 2, 0)
```

- `Op1 < Op2`: every component is no larger and at least one is smaller, so Op1 causally precedes Op2 under the stated vector-clock model.
- `Op1 || Op3`: Op1 has a larger third component but smaller second component, so neither dominates the other.
- `Op2 || Op3`: Op2 has larger first and third components but a smaller second component.

### Numerical problem 2: session validation

The client requires `(4, 2)`. S1 has `(4, 1)` and S2 has `(5, 3)`.

S2 may serve the read because both its components meet the requirement. S1 must fetch the missing second-origin update or block. Its first component matching 4 does not compensate for its second component being too small.

### Conceptual questions

Uncoordinated eventual-consistency caches alone do not guarantee sequential consistency: independent stale observations can form histories with no legal shared sequence. Additional ordering, validation, or routing can constrain a caching system sufficiently; caching itself does not make sequential consistency impossible.

Monotonic Reads concerns what a client has already **read**, including others' writes. Read-Your-Writes concerns what that client has **written**, even before its first read. A client can need one guarantee without having any history relevant to the other.

The final resource page, PDF page 46, lists mutual exclusion and leader election readings. Those are supplementary entries, not further requirements for this session-proxy lab.

## 7. Actual assignment and solution (pages 42-43)

### Required work

Build a client proxy between applications and three replicated stores S1, S2, and S3 in Python, Java, or Go. Track `V_read` and `V_write`; enforce Monotonic Reads and Read-Your-Writes; simulate replica delay/divergence; and show the proxy stalling and fetching missing write sets for stale destinations.

The grading weights are session vectors and validation 40%, the two guarantees 30%, fetching and recovery 20%, and documentation/design report 10%.

### Implemented solution

The Python implementation uses three independent in-memory stores and a deterministic logical-time message queue. Every write includes its origin, origin sequence, key/value, and vector context. Replicas keep applied histories so any reachable peer can supply missing updates.

On a read, the proxy computes `merge(V_read, V_write)`, validates the selected replica, fetches missing origin prefixes if necessary, and checks again before returning data. It then updates `V_read`. If the necessary writes are unreachable, it raises `UnavailableError` and leaves the read context unchanged. Retrying after reachability returns completes the repair.

Writes increment the receiving replica's own component, are applied locally before acknowledgement, and are delivered to peers after a configured number of logical ticks. The proxy also aligns the target with the existing session history before writes. Dependency buffering prevents later writes from advancing clocks over missing predecessors, and duplicate operations have no additional effect.

Concurrent writes use a documented deterministic conflict rank so all replicas choose the same value after receiving the same write set. This is a simulator policy, not a claim that vector clocks naturally order concurrent events. Details and limitations are in `DESIGN_REPORT.md`.

### Expected Alice example

```text
1. Client writes profile='Alice' to S1.
2. S1 acknowledges; client V_write becomes (1, 0, 0).
3. Client selects S2, whose vector is still (0, 0, 0).
4. Proxy detects that S2 does not dominate (1, 0, 0).
5. Proxy stalls the read, fetches W1 from S1, and applies it to S2.
6. S2 advances to (1, 0, 0), returns 'Alice', and V_read becomes (1, 0, 0).
```

For Monotonic Reads, a separate observer first reads Alice from S1, without writing anything. Its `V_read` becomes `(1, 0, 0)` while `V_write` remains zero. Selecting stale S3 triggers the same repair from the read context. This demonstrates Monotonic Reads independently of Read-Your-Writes.

### Run and deliverables

From `Day 7`:

```bash
python3 -m unittest -v
python3 run_demo.py --output execution_trace.log
```

The demo asserts the Alice example, independent observer behavior, delayed eventual propagation, and blocked-read recovery. `test_session_proxy.py` additionally checks vector comparisons, session isolation, dependency order, duplicate delivery, backup fetching, convergence, and invalid requests. The README contains the usage guide, and the design report maps implementation evidence to every rubric item.

The simulation uses direct method calls and logical ticks rather than operating-system sockets and real-time sleeps. This matches the stated simulator requirements and makes the outcomes reproducible. It does not model durable crash recovery or implement a production database.
