# Day 2 lab solution: mini-DHT registry and messaging client

This reference implementation uses TCP sockets and newline-delimited JSON. A node has a stable logical name whose SHA-1 hash is its Chord ID. Its network address is a mutable registry value, so the node can restart at a new address without changing its identity.

## Run the demo

Open three terminals in this directory:

```bash
python3 mini_dht.py --name alpha --port 5101
python3 mini_dht.py --name beta  --port 5102 --seed 127.0.0.1:5101
python3 mini_dht.py --name gamma --port 5103 --seed 127.0.0.1:5101 --seed 127.0.0.1:5102
```

Send a message to `gamma` by resolving its stable name through either bootstrap node:

```bash
python3 client.py --seed 127.0.0.1:5101 --seed 127.0.0.1:5102 \
  --target gamma --message "hello through the DHT"
```

The client prints the lookup path, resolved address, and delivery result. The `gamma` terminal prints the message.

## Demonstrate address change

Stop `gamma`, then restart the same logical node on a different port:

```bash
python3 mini_dht.py --name gamma --port 5199 \
  --seed 127.0.0.1:5101 --seed 127.0.0.1:5102
```

Run the same client command. The registry entry for `gamma` is updated and replicated, so the client discovers port `5199` without changing the target name.

## How it meets the assignment

- **Sockets:** nodes and clients communicate through TCP sockets.
- **O(log N) routing:** each node builds Chord finger entries for offsets `2^i`; routing chooses the closest preceding finger, reducing the remaining identifier distance geometrically.
- **Flat-name resolution:** `SHA1(name) mod 2^8` maps a logical name to the node responsible for its registry record.
- **Fault tolerance:** records are stored at the owner and the next two successors; the client accepts multiple bootstrap nodes and lookup skips unreachable hops.
- **Location independence:** the stable name/ID is separate from the mutable `host:port` address.

For a production system, add authenticated membership, periodic stabilization/heartbeats, durable storage, TLS, timeouts with backoff, versioned records, and conflict resolution.
