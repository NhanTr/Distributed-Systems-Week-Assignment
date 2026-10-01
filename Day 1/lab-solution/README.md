# Practical Lab: Architecting Latency Hiding

This implementation compares two registration paths:

1. **Server-only validation** sends every submission over the network and waits for the server to report errors.
2. **Local + server validation** rejects malformed input in the browser and sends only locally valid data. The server remains authoritative and validates again for security and correctness.

## Run

Requires Python 3.9 or newer and no third-party packages.

```bash
cd lab-solution
python3 server.py
```

Open `http://localhost:3000`. The default simulated WAN delay is 1200 ms. Change it with:

```bash
WAN_DELAY_MS=2000 python3 server.py
```

## Experiment

Use invalid data such as `A`, `wrong`, and `short` in both forms.

- Server-only: one request and approximately one WAN delay before feedback.
- Local + server: zero requests and near-instant feedback.

Then use valid data such as `Ada`, `ada@example.com`, and `password1`.
Both paths contact the server because local validation cannot replace authoritative server validation.

For `n` failed correction attempts and round-trip time `RTT`, the avoidable waiting time is approximately:

```text
saved time = n * RTT
```

At a 1200 ms simulated RTT and three invalid attempts, local validation avoids about 3600 ms and three network requests.

## Mapping to Peter Deutsch's false assumptions

Local validation directly mitigates:

- **Latency is zero**: invalid formatting no longer incurs a remote round trip.
- **Bandwidth is infinite**: malformed submissions are not transmitted.
- **Transport cost is zero**: fewer requests reduce radio, infrastructure, and monetary cost.
- **The network is reliable** (partially): basic feedback still works while offline.

It does **not** solve network security, heterogeneity, changing topology, or multiple administrative domains. It also cannot guarantee uniqueness, authorization, current business rules, or successful persistence; those checks must remain on the server.
