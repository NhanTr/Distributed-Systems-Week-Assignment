# Lecture 01 Summary: The Illusion of One

## Central idea

A distributed system is a collection of independent computers that appears to users as one coherent system. Middleware sits between applications and local operating systems to hide heterogeneity, location, communication, failures, and other distribution details.

## Four goals

1. **Resource accessibility** - Share remote hardware, storage, data, and services for economic value and collaboration. The cost is a larger security surface, so access control, authentication, and privacy become essential.
2. **Distribution transparency** - Hide selected physical facts of the network:
   - Access: different representations and access methods.
   - Location: where a resource is located.
   - Migration: that a resource moved.
   - Relocation: that a resource is moving while in use.
   - Replication: that multiple copies exist.
   - Concurrency: that users/processes share a resource simultaneously.
   - Failure: crashes and recovery.
3. **Openness** - Use standard protocols and complete interface definitions so components interoperate, remain portable, and can be replaced. Separate **mechanism** (how a capability works) from **policy** (what behavior is selected).
4. **Scalability** - Grow across size, geographic distance, and administrative domains. Centralized services, data, or algorithms become bottlenecks and single points of failure.

## Limits and design techniques

Complete transparency is neither possible nor always desirable. Speed-of-light delay makes WAN latency unavoidable, retrying can make failures more expensive, and context-aware applications may need to expose location. Lamport's warning is that a remote failure can stop work even when the failed machine was previously unknown to the user.

The four rules for decentralized algorithms are:

1. No machine has complete system state.
2. Decisions are based on local information.
3. The failure of one machine does not ruin the algorithm.
4. There is no implicit global clock.

A central clock violates decentralization because it creates a global dependency and a single point of failure; real clocks also drift and messages take nonzero, variable time.

Peter Deutsch's eight false assumptions are: the network is reliable; the network is secure; the network is homogeneous; topology does not change; latency is zero; bandwidth is infinite; transport cost is zero; and there is one administrator.

Three major scaling techniques are:

- **Latency hiding** - Prefer asynchronous work or move suitable work to the client.
- **Distribution** - Partition data and computation, as DNS partitions naming into zones.
- **Replication and caching** - Put copies near demand to balance load and reduce delay, at the cost of consistency complexity.

## Distributed-system venues

- **High-performance and grid computing** - Clusters use homogeneous machines and fast LANs; grids combine heterogeneous resources across organizations. Foster's grid model separates fabric, connectivity, collective coordination, and applications.
- **Distributed information systems** - Transaction monitors coordinate operations across databases. Enterprise integration uses RPC, RMI, and message-oriented middleware.
- **Pervasive and embedded systems** - Mobile, wireless, battery-powered nodes operate with instability and little central administration. Sensor networks can aggregate and filter data in-network to reduce communication and energy use.

## Architecture styles

Logical software styles include layered, object-based, event-based publish/subscribe, and data-centered shared repositories. System organizations range from centralized client-server to decentralized peer-to-peer overlays. Structured P2P overlays use deterministic routing such as DHTs; unstructured overlays use randomized neighbors, partial views, gossip, and search. Hybrid systems combine central coordination with decentralized transfer: edge servers place content near users, while BitTorrent uses a tracker for discovery and a peer swarm for transfer.

## Self-management

At geographic and administrative scale, manual control is insufficient. Adaptive systems use a feedback loop:

1. **Monitor** local and global metrics.
2. **Analyze** them against desired behavior and policy.
3. **Act** by changing routing, placement, replication, or components.

Astrolabe collects node state through gossip and hierarchically aggregates it with SQL-like queries; those summaries feed analysis and expose actionable state to higher-level applications. Globule evaluates replication policies and changes them per page. Jade detects failures, adds replacement nodes, and reconfigures components without stopping the system.

## Concept-review answers

### 1. Mapping all seven transparencies to a real system

Consider a global cloud document editor:

- Access: the same document API works from web and mobile clients.
- Location: users do not know which region stores the document.
- Migration: storage can move to another server or region.
- Relocation: an active session can be reassigned while the user edits.
- Replication: regional replicas appear to be one document.
- Concurrency: many editors can update the document together.
- Failure: replicas and failover hide a crashed process or server.

Conflicts appear when one transparency weakens another goal: hiding replication conflicts with immediately exposing concurrent edits; hiding failure can cause long retry delays; hiding location can conflict with data-residency policy and context-aware routing; migration or relocation may briefly weaken consistency or increase latency. Therefore transparency is a design choice, not an absolute requirement.

### 2. Decentralized rules and the clock

The four rules are local knowledge, local decisions, tolerance of individual-machine failure, and no implicit global clock. A central clock violates the last three in practice: all decisions depend on a central service, its failure can halt progress, and consulting it requires network communication whose delay is variable.

### 3. Policy versus mechanism

A stable, general mechanism exposes capabilities through neutral interfaces; replaceable policies decide how those capabilities are used. Because policy can change without rebuilding the mechanism, independent implementations can interoperate and evolve. This directly supports openness, portability, and extensibility.

### 4. Astrolabe and Analyze

Astrolabe gossips local node observations and rolls them up into hierarchical summaries such as counts, averages, minima, and maxima. The Analyze phase consumes this compact global view to detect abnormal state, compare measurements with policy, and decide whether to trigger reallocation or alarms.

## Lab solution and result

The dependency-free Python implementation is in `lab-solution/`. It offers two equivalent forms:

- Server-only validation sends faulty input across a simulated WAN and receives errors after the configured delay.
- Local validation checks formatting immediately, records zero network requests for malformed data, and sends only locally valid input. The server always validates again and remains authoritative.

With the default 1200 ms delay, verification produced:

- Invalid server submission: HTTP 422 in about 1.20 s.
- Valid server submission: HTTP 201 in about 1.20 s.
- Invalid local submission: immediate feedback with zero requests.

For `n` invalid attempts and round-trip time `RTT`, approximate time saved is `n * RTT`. Local validation mitigates the false assumptions that latency is zero, bandwidth is infinite, and transport cost is zero. It partially mitigates unreliable connectivity by preserving basic feedback offline. It does not replace server validation or solve security, topology, heterogeneity, or administrative-boundary problems.
