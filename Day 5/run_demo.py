#!/usr/bin/env python3
"""Run commit, abort, and WAL-recovery scenarios for the 2PC lab."""

from __future__ import annotations

import argparse
from pathlib import Path

from two_phase_commit import (
    Coordinator,
    JsonLineServer,
    Participant,
    TraceLog,
    WriteAheadLog,
    rpc,
)


WAL_FILENAMES = ("coordinator.wal", "P1.wal", "P2.wal", "P3.wal")


def run_demo(wal_dir: Path) -> list[str]:
    """Execute one committed and one aborted transaction, then recover P3."""

    wal_dir = Path(wal_dir)
    wal_dir.mkdir(parents=True, exist_ok=True)
    for filename in WAL_FILENAMES:
        (wal_dir / filename).unlink(missing_ok=True)

    trace = TraceLog()
    participants: dict[str, Participant] = {}
    participant_servers: dict[str, JsonLineServer] = {}
    coordinator_server: JsonLineServer | None = None

    try:
        for node_id in ("P1", "P2", "P3"):
            participant = Participant(
                node_id,
                WriteAheadLog(wal_dir / f"{node_id}.wal", node_id),
                abort_transactions={"102"} if node_id == "P3" else None,
                trace=trace,
            )
            server = JsonLineServer(participant.handle)
            server.start()
            participants[node_id] = participant
            participant_servers[node_id] = server

        coordinator = Coordinator(
            {
                node_id: server.address
                for node_id, server in participant_servers.items()
            },
            WriteAheadLog(wal_dir / "coordinator.wal", "COORD"),
            trace=trace,
        )
        coordinator_server = JsonLineServer(coordinator.handle)
        coordinator_server.start()

        for tx_id in ("101", "102"):
            response = rpc(
                coordinator_server.address,
                {"type": "EXECUTE", "tx_id": tx_id},
            )
            if response.get("type") != "RESULT":
                raise RuntimeError(f"coordinator rejected transaction {tx_id}: {response}")

        participant_servers["P3"].stop()
        recovered_p3 = Participant(
            "P3",
            WriteAheadLog(wal_dir / "P3.wal", "P3"),
            trace=trace,
        )
        trace.emit(
            "SYSTEM",
            "P3 restarted from WAL: "
            f"Tx 101={recovered_p3.states['101']}, "
            f"Tx 102={recovered_p3.states['102']}",
        )
        return trace.snapshot()
    finally:
        if coordinator_server is not None:
            coordinator_server.stop()
        for server in participant_servers.values():
            server.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wal-dir",
        type=Path,
        default=Path("wal"),
        help="directory for runtime write-ahead logs (default: wal)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional file to receive the execution trace",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    lines = run_demo(arguments.wal_dir)
    rendered = "\n".join(lines) + "\n"
    print(rendered, end="")
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
