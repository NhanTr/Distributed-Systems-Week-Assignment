# Lecture 03: Time and Synchronization - Summary and Lab Solution

## Lecture summary

Distributed systems have no perfect global clock: each machine has an independent
oscillator and messages experience variable delay. **Clock skew** is the difference
between clock readings at an instant; **clock drift** is the difference in their
rates. If each clock's drift is bounded by `rho`, two clocks may diverge at up to
`2 * rho`, so maintaining precision `delta` requires resynchronization within
`R <= delta / (2 * rho)`.

Physical-clock techniques serve different environments:

- **Cristian's algorithm** asks a UTC server for its time and estimates one-way
  delay as half the round-trip time. It is simple, but asymmetric delay creates
  error and the server may be a single point of failure.
- **Berkeley's algorithm** lets a master poll nodes, reject outliers, compute an
  average, and send relative corrections. It gives internal agreement without UTC.
- **NTP** uses a hierarchy of strata and four timestamps. It estimates network
  delay and clock offset, then filters samples to reduce jitter error.
- **RBS** has receivers compare the arrival time of the same wireless broadcast,
  eliminating uncertain sender-side delay and enabling high precision in sensor
  networks.

When wall-clock time is unnecessary, logical time captures ordering. Lamport's
`a -> b` relation follows process order, send-before-receive order, and transitivity.
Each process increments a scalar clock for local/send events; on receive it sets
`L = max(L, message.L) + 1`. Thus `a -> b` implies `L(a) < L(b)`, but the converse
is false. `(Lamport timestamp, process ID)` can impose a deterministic total order,
although it cannot identify concurrency.

Vector clocks retain causal history. Each process maintains one counter per process,
increments its own component, piggybacks the vector on messages, and merges with a
component-wise maximum on receive before incrementing its own component. For vectors
`A` and `B`, `A < B` means every component of `A` is no greater and at least one is
smaller. If neither `A <= B` nor `B <= A`, the events are concurrent. This enables
conflict detection in multi-master stores, though vectors cost `O(N)` per timestamp.

At enterprise scale, Dynamo-style version vectors preserve concurrent siblings for
later reconciliation. Google Spanner's TrueTime instead exposes a bounded physical
time interval based on GPS and atomic clocks; commit wait delays visibility until
the uncertainty interval has passed, supporting external consistency.

## Review-problem answers

For NTP timestamps `T0=1000`, `T1=1040`, `T2=1080`, `T3=1140` ms:

- Network delay: `theta = (T3-T0) - (T2-T1) = 140 - 40 = 100 ms`.
- Clock offset: `d = ((T1-T0) + (T2-T3)) / 2 = (40 - 60) / 2 = -10 ms`.
  Under the slide's server-minus-client convention, the server is estimated to be
  10 ms behind the client, so the client should adjust by `-10 ms` (preferably slew).

For `VC(a)=[2,1,3]`, `VC(b)=[3,1,4]`, and `VC(c)=[2,2,2]`:

- `a -> b`, because every component of `VC(a)` is `<= VC(b)` and two are smaller.
- `a || c`, because `a` is ahead in component 3 while `c` is ahead in component 2.

## Programming-lab solution

`logical_clock_simulator.py` implements three worker threads (`P0`, `P1`, `P2`).
Each worker owns its Lamport scalar and three-entry vector. Workers communicate only
through one thread-safe inbox queue per process; logs and completion notifications
flow through a separate output queue. No clock state is shared globally.

Each random event is local computation, sending, or receiving. A send carries an
immutable snapshot of both timestamps. A receive:

1. compares the incoming vector with the local vector *before merging* and logs a
   conflict if neither vector dominates;
2. applies `L = max(local_L, message_L) + 1`;
3. performs component-wise vector maximum, then increments the receiver's entry.

After random generation, workers drain their queues so sent messages are not lost.
The coordinator inserts FIFO stop sentinels only after every worker has stopped
sending.

### Architecture

```text
                 queue[P0]      queue[P1]      queue[P2]
                     |              |              |
                     v              v              v
               +-----------+  +-----------+  +-----------+
               | P0 thread |  | P1 thread |  | P2 thread |
               | L0, VC0   |  | L1, VC1   |  | L2, VC2   |
               +-----+-----+  +-----+-----+  +-----+-----+
                     \              |              /
                      +-------------+-------------+
                                    |
                              output queue
                                    |
                                    v
                              coordinator/log
```

### Run and test

```bash
python3 logical_clock_simulator.py --steps 10 --seed 7
python3 -m unittest -v
```

The seed fixes each worker's choices, while actual log interleaving may still vary
because the workers are genuinely concurrent.
