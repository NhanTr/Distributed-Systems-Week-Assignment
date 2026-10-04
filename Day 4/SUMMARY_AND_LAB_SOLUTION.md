# Lecture 04: Processes, Coordination, and Elections

## Knowledge summary

### Processes, threads, and server architecture

A process owns an isolated address space, security context, page tables, file
descriptors, and other OS resources. This isolation improves fault containment but
makes process creation and context switching expensive. Threads are independent
execution paths inside one process: they share code, global memory, and resources,
while retaining private registers, stack, and program counter. Their lower switching
cost makes them useful for hiding network I/O latency.

Distributed clients use threads to fetch resources concurrently and keep user
interfaces responsive. Servers can process requests sequentially, dispatch work to
a thread pool, or use an event-driven finite-state machine with non-blocking I/O.
Stateless servers are easy to retry and scale horizontally; stateful servers support
sessions and locks but require replication or recovery. Soft state limits stale
sessions with TTL expiration. A typical three-tier cluster separates the front-end
load balancer, application servers, and back-end data services.

### Virtualization and code migration

Virtualization decouples software from physical hardware for consolidation,
multi-tenancy, isolation, and legacy support. Type-1 hypervisors run directly on
hardware; Type-2 hypervisors run above a host OS. Virtual machines emulate hardware
and include a guest kernel, while containers share the host kernel and isolate
namespaces and resource limits, giving faster startup and lower overhead.

Code migration moves computation closer to data, balances load, or transfers code
to clients. A running process contains code, resource references, and execution
state. Weak mobility transfers code and initial parameters and restarts execution;
strong mobility also transfers the stack, registers, and program counter so work
resumes at the interruption point. Resources may be moved, rebound by reference or
type, copied, or accessed through a network proxy depending on whether they are
unattached, fastened, or fixed to a host.

### Distributed mutual exclusion

Mutual exclusion must provide safety, liveness, ordering, and acceptable overhead
without shared memory or a global clock.

- A centralized coordinator uses `REQUEST`, `GRANT`, and `RELEASE`: only three
  messages and two propagation delays, but the coordinator is a bottleneck and SPOF.
- Decentralized voting replicates coordinators and requires a majority quorum. It
  survives minority failures but split votes and retries can cause starvation.
- Ricart-Agrawala multicasts a Lamport-timestamped request and waits for `N-1` replies.
  It costs `2(N-1)` messages, removes the coordinator, but one failed peer can block
  progress unless failure handling is added.
- Token Ring circulates one token in logical order, guaranteeing fairness and a
  bounded wait of at most `N-1` hops. Token loss risks duplicate regeneration, while
  a crashed node requires its predecessor to bypass and repair the ring.

There is no universally best algorithm. Modern systems often expose a centralized
lock service while replicating that service with Raft or Paxos.

### Leader election

Leader election chooses the highest-priority active process after coordinator
failure. In Bully, a detector sends `ELECTION` to higher IDs. Any higher process
replies `OK` and starts its own election. A process that receives no higher reply
broadcasts `COORDINATOR`. Bully is fast when a high-ID node initiates, but its worst
case is `O(N^2)` messages and false failure suspicions can trigger expensive elections.

Ring election passes an ID list around a logical ring, chooses the maximum, then
circulates the coordinator announcement. Its cost is predictably about `2N` messages,
but sequential traversal increases latency and dead nodes must be bypassed. Wireless
networks can instead build a temporary spanning tree and aggregate the best candidate
upward. ZooKeeper's ZAB favors the candidate with the newest epoch and transaction
history, ensuring the leader contains committed state.

### Review answers

Live VM migration needs strong mobility because the VM must resume with its memory,
CPU registers, stack, and instruction position intact. Browser JavaScript normally
uses weak mobility because code and initial data can be downloaded and started in a
fresh execution environment.

Token Ring has two main failures: a lost token is detected with a circulation timer
and recovered by controlled regeneration; a crashed node breaks the path and is
recovered by changing the predecessor's next pointer to bypass that node.

If two Ricart-Agrawala requests have the same Lamport time, the process ID breaks the
tie; the smaller `(timestamp, process ID)` pair enters first. If the lowest process
starts a Bully election, cascading elections produce `O(N^2)` messages.

## Lab solution

The solution uses five Python process threads with real TCP sockets and implements
the Bully algorithm. `P5` initially sends heartbeats as coordinator. The cluster then
closes `P5`'s socket and stops its thread. `P2` detects the missed heartbeat first,
contacts higher IDs, and is displaced by `P3` and `P4`. Since `P5` cannot answer
`P4`, `P4` declares victory and sends `COORDINATOR` to `P1`, `P2`, and `P3`.

Files:

- `bully_election_simulator.py`: executable socket-based simulator.
- `test_bully_election_simulator.py`: end-to-end integration tests.
- `execution_trace.log`: actual trace generated by the simulator.
- `ARCHITECTURE_REPORT.md`: two-page architecture report with an explicit page break.

Run from the `Day 4` directory:

```bash
python3 bully_election_simulator.py --output execution_trace.log
python3 -m unittest -v
```
