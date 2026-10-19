# Day 6: Consistency and Replication

Source: `Lect06_consistency_and_replication.pdf`, 47 PDF pages. Page numbers below refer to PDF pages, which differ from the slide numbers because the file includes section dividers.

## Assignment status

The supplied PDF does **not contain a final lab specification**. Its roadmap mentions practical lab specifications, but the actual ending is:

- Page 44: cache coherence and caching execution modes.
- Page 45: Cassandra, Bigtable, and Chubby case studies.
- Pages 46-47: recommended supplementary resources.

The `Day 6` folder initially contained only this PDF, with no starter code or separate assignment. Consequently, no assignment implementation or claimed final-lab answer is included here. The worked solutions at the end are study exercises derived from the lecture, not an invented assignment. The actual lab prompt is required to determine the required algorithm, interface, scenarios, and deliverables.

## 1. Why replicate data? (pages 4-6)

Replication keeps copies of data on multiple servers. It improves availability during failures, supports geographic redundancy, reduces access latency, and distributes read traffic. The cost is coordinating writes and propagating updates. More replicas can improve reads while increasing write latency and network traffic.

A consistency model specifies what values applications may observe and what ordering of operations the store guarantees. Replication is the mechanism; consistency is the contract.

The lecture introduces two trade-offs:

- **CAP:** during a network partition, a system cannot guarantee both linearizable consistency and availability for every request. Partition tolerance is a condition distributed systems must handle, rather than a feature that simply removes the trade-off.
- **PACELC:** during a partition, choose between availability and consistency; during normal operation, consider the latency cost of maintaining consistency.

Use the weakest model that still preserves the application's required behavior. A financial invariant and an eventually updated social feed have different coordination needs.

Notation: `Wi(x)a` means process `Pi` writes value `a` to `x`; `Ri(x)b` means its read returns `b`. Process timelines progress from left to right.

## 2. Data-centric consistency models (pages 7-17)

These models describe permitted observations across processes.

| Model | Required ordering or synchronization | Main implication |
| --- | --- | --- |
| Strict | Every read returns the most recent write in absolute physical time. | An idealized baseline with instantaneous global visibility. |
| Sequential | All operations fit one total order that preserves each process's program order. | Processes agree on one explanation, without a real-time ordering requirement. |
| Causal | All processes preserve the order of causally related writes. | Independent concurrent writes may be observed in different orders. |
| FIFO / PRAM | Writes from each individual writer appear in that writer's order. | Different writers' updates may be interleaved differently. |
| Weak | Synchronization operations establish when ordinary data must be consistent. | Local reads and writes can proceed between synchronization points. |
| Release | Separate acquire and release operations control visibility. | Acquire obtains the required prior updates; release publishes preceding updates. |
| Entry | Synchronization objects are explicitly associated with protected data. | Acquiring a lock updates its associated data, enabling finer granularity. |

### Sequential consistency

To check an execution, try to construct a single sequence containing every read and write. Preserve program order, and ensure each read returns the last preceding write to that item in the sequence.

If two processes each read `b` then `a`, independent writes of `b` and `a` can be explained by ordering the write of `b` before the write of `a`. If one reader sees `a` then `b` while another sees `b` then `a`, and each value was written only once, there is no shared ordering that explains both histories.

Sequential consistency differs from linearizability: linearizability also preserves the real-time order of nonoverlapping operations. The lecture's strict consistency baseline should not be treated as interchangeable with either term.

### Causal consistency and FIFO

Causality includes local program order, a read observing a write, and transitive dependencies. If `P2` reads `a` and then writes `b`, the write of `a` causally precedes the write of `b`. Everyone must observe that order under causal consistency.

FIFO alone does not preserve this dependency across writers. It preserves the order of each writer's own writes. TCP preserves bytes within one connection; a complete FIFO replication protocol still needs to handle multiple channels, reconnects, and application-level delivery.

Vector clocks can distinguish causal dependency from concurrency. Lamport timestamps preserve the forward implication `a -> b` implies `L(a) < L(b)`, but the reverse implication does not hold; a smaller Lamport timestamp alone does not prove causality.

### Synchronization-based models

Under weak consistency, accesses to synchronization variables are sequentially consistent. Prior writes must complete before the relevant synchronization completes, and later data accesses wait for earlier synchronization accesses.

Release consistency distinguishes acquiring permission from releasing it. Eager propagation sends updates at release; lazy propagation postpones communication until another process needs the updates. Entry consistency explicitly maps synchronization objects to data, so unrelated objects need not be synchronized together. This explicit association is the key distinction; ordinary release consistency should not be assumed to provide the same object-level mapping automatically.

## 3. Eventual consistency and client sessions (pages 19-28)

Eventual consistency promises convergence after updates stop, assuming updates can eventually propagate and conflicts are resolved consistently. It permits temporary disagreement. It does not, by itself, guarantee any particular convergence deadline or unconditional availability under every failure.

A client moving between replicas can observe surprising results even if those replicas eventually converge. Session guarantees constrain that client's experience:

| Guarantee | Required behavior | Example anomaly prevented |
| --- | --- | --- |
| Monotonic reads | Later reads include the relevant updates already observed by the client. | An inbox appears to lose messages after switching servers. |
| Monotonic writes | The client's writes are applied in issue order. | Configuration is applied before its required library update. |
| Read-your-writes | Later reads reflect the client's earlier writes, or their subsequent effects. | A saved profile edit disappears on refresh. |
| Writes-follow-reads | A write carries dependencies on updates previously read. | A reply becomes visible before its parent post. |

Clients can carry separate read and write version vectors or write-set contexts. A replica serves a request only after its state includes the dependencies required by the selected guarantee. Vector comparison is componentwise, not ordinary scalar comparison.

The dependencies differ by guarantee: monotonic reads use observed history; read-your-writes uses the client's write history; monotonic writes orders later writes after earlier writes; writes-follow-reads orders writes after observed history. Enforcing all four generally needs both histories.

If the selected replica is behind, the system can fetch missing updates, route to a sufficiently current replica, or wait. If required history is unreachable, maintaining the guarantee may require rejecting or delaying the request. Sticky routing helps only while the selected replica retains the necessary history; failover requires additional handling.

## 4. Replica placement and propagation (pages 30-37)

**Server placement** chooses infrastructure locations. **Content placement** chooses which objects are stored at those locations. The lecture's greedy placement method first selects a site with low aggregate client distance, then adds sites that most reduce remaining distance. It is a heuristic, not a general guarantee of the globally optimal placement.

Replica categories are permanent replicas, server-initiated replicas created in response to demand, and client-initiated caches.

| Propagation content | Mechanism | Typical advantage |
| --- | --- | --- |
| Invalidation | Notify recipients that their cached value is stale. | Small messages when writes are frequent and reads are rare. |
| Full state | Send the new object value. | Immediate subsequent reads when reads dominate. |
| Operation | Send an operation and its arguments. | Small updates to large objects, provided execution semantics are safe. |

Push proactively sends updates and reduces freshness delay at the cost of potentially unnecessary traffic. Pull fetches updates when needed or on a schedule, saving some traffic but adding fetch latency and a stale interval. Polling itself still consumes traffic even when values do not change.

### Gossip and deletion

Anti-entropy periodically reconciles peers, using push, pull, or push-pull exchanges. Rumor spreading actively forwards new updates and probabilistically stops forwarding to control traffic. Rumor termination can leave some nodes uninformed; continuing anti-entropy can repair the missing updates. Convergence depends on connectivity, peer selection, and conflict handling.

A deletion must propagate as a versioned **tombstone**. Simply erasing the item makes an old replica appear to hold missing data, which can resurrect the deleted value. Tombstone removal requires a safe dissemination or repair policy; an arbitrary timer alone is insufficient when replicas can remain offline longer than that timer.

The CDN example combines distributed edge caches, pull caching with TTLs, and origin-triggered invalidation. Anycast routing follows network routing policy and does not necessarily choose the geographically nearest server.

## 5. Replication protocols (pages 39-42)

**Primary-backup:** a primary orders updates and propagates them to backups. Synchronous acknowledgements add latency; asynchronous backups can lag. Read routing, failover, and the acknowledgement policy determine the actual consistency guarantee. A single primary by itself does not establish every claimed guarantee.

**Local-write primary migration:** move exclusive write authority and the required state to the node performing updates. Safe migration must ensure old and new primaries cannot both accept conflicting writes.

**Active replication / state machine replication:** replicas start from identical state and execute deterministic operations in the same total order. Ordering requires an appropriate agreement protocol; merely broadcasting to every replica is insufficient. Sources of nondeterminism, including local clocks and random choices, must be controlled.

### Quorum mathematics

For `N` equal-vote replicas, read quorum `R`, and write quorum `W`:

```text
1 <= R <= N
1 <= W <= N
R + W > N       (read/write intersection)
2W > N          (write/write intersection)
```

The minimum read quorum for a selected valid write quorum is `Rmin = N - W + 1`. The minimum write quorum is `floor(N / 2) + 1`.

Minimum intersection sizes are `R + W - N` for a read and write quorum and `2W - N` for two write quorums. With fixed membership and otherwise reachable surviving replicas, reads can tolerate at most `N - R` unavailable replicas and writes at most `N - W`.

Overlap is necessary for the lecture's quorum protocol, but overlap equations alone do not implement linearizable storage. A complete protocol also needs correct version selection, ordering of concurrent writes, handling of incomplete writes, and recovery rules. In a weighted system, the same inequalities apply to total vote weight rather than the number of servers.

## 6. Cache coherence and case studies (pages 44-47)

Cache invalidation removes stale entries; cache update sends replacement values. Write-through forwards writes to the backing store before completion according to its policy. Write-back acknowledges a cache update before later propagation, so a volatile cache crash can lose acknowledged data.

The lecture uses Cassandra to illustrate configurable read/write consistency and gossip, Bigtable to illustrate row-level consistency, and Chubby to illustrate consensus-backed coordination. These are architectural teaching examples, rather than a configuration guide. In particular, quorum reads and writes should not be interpreted as automatically providing linearizable conditional updates or arbitrary multirow transactions.

The final two pages list readings on sequential and causal consistency, session guarantees, PACELC, weighted voting, state machine replication, gossip, Dynamo, and Cassandra. They contain no lab instructions or evaluation rubric.

## 7. Worked study solutions

### Exercise A: Evaluate the lecture's five-replica configurations

| N | R | W | R + W > N? | 2W > N? | Valid? | Read failures tolerated | Write failures tolerated |
| ---: | ---: | ---: | --- | --- | --- | ---: | ---: |
| 5 | 1 | 5 | 6 > 5 | 10 > 5 | Yes | 4 | 0 |
| 5 | 3 | 3 | 6 > 5 | 6 > 5 | Yes | 2 | 2 |
| 5 | 2 | 2 | 4 > 5 is false | 4 > 5 is false | No | Not applicable | Not applicable |

For `(5, 2, 2)`, a write can reach `{A, B}` while a read uses `{C, D}` and misses the update. Two writers can also use disjoint sets `{A, B}` and `{C, D}`.

All valid `(R, W)` pairs for `N = 5` under these rules are:

```text
W = 3: (3,3), (4,3), (5,3)
W = 4: (2,4), (3,4), (4,4), (5,4)
W = 5: (1,5), (2,5), (3,5), (4,5), (5,5)
```

Balanced majority is `(R, W) = (3, 3)`. In a `3 + 2` network partition, only the three-node side can form either quorum. The two-node side must wait or reject requests that require those quorums.

### Exercise B: Read from overlapping quorums

Suppose a completed, serialized write of `x = 20` at version 2 reaches `{A, B, C}`. Nodes `{D, E}` still store `x = 10` at version 1. A read of `{C, D, E}` receives versions `{2, 1, 1}`.

The answer is `20`, selected from the highest valid version, not `10` selected by majority of returned values. The quorums overlap at `C`; most members of a read quorum do not need to hold the newest value. This example assumes no concurrent or incomplete writes; those require the additional protocol rules described above.

### Exercise C: Distinguish causal and FIFO histories

```text
P1: W(x)a
P2: R(x)a -> W(x)b
P3: R(x)b -> R(x)a
```

Assuming each value is written once, this history violates causal consistency because `P2`'s read establishes `W(x)a -> W(x)b`, but `P3` observes the reverse order. It can satisfy FIFO because `a` and `b` come from different writers, each issuing only one write. It also violates sequential consistency because the read constraints and program order cannot fit one global sequence.

If `P2` writes `b` independently without reading `a`, readers may see the two writes in opposite orders under causal consistency. A shared sequential order is still required for sequential consistency.

### Exercise D: Enforce session guarantees across a server hop

Client `C` writes profile version 4 at replica A, then requests the profile from B, which has only version 3. Read-your-writes requires B to obtain version 4 or a state incorporating it before responding, or to route the request to a suitable replica. Returning version 3 violates the guarantee.

If `C` previously read version 4 written by somebody else, the analogous protection is monotonic reads. If it then writes a reply based on that read, writes-follow-reads carries the dependency into the reply. If it issues a second profile update, monotonic writes requires the first update to precede the second.

### Exercise E: Prevent a deleted item from returning

1. A and B initially hold `x = old` at version 1.
2. A deletes `x` by storing a tombstone at version 2.
3. During reconciliation, B's version 1 cannot overwrite version 2.
4. B stores the tombstone and stops returning the old value.
5. Keep the tombstone until the system's repair and retention rules make collection safe.

This preserves the deletion when a stale replica reconnects. Plain absence of `x` on A would not carry enough information to distinguish deletion from a missing update.

## Next step for the actual assignment

Provide the missing lab page or a separate assignment statement. This summary can then be extended with the exact solution, implementation, tests, execution trace, and run instructions matching the instructor's requirements.
