#!/usr/bin/env python3
"""Registry lookup and messaging client for mini_dht.py."""

from __future__ import annotations

import argparse
import json
import socket

from mini_dht import hash_id, parse_endpoint, rpc


def lookup(seeds: list[tuple[str, int]], name: str) -> dict:
    request = {"action": "GET", "key": hash_id(name), "name": name}
    errors = []
    for seed in seeds:
        try:
            response = rpc(seed, request)
            if response.get("ok"):
                return response
            errors.append(response.get("error", "lookup failed"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    raise RuntimeError("all bootstrap nodes failed: " + "; ".join(errors))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="append", required=True, help="host:port; repeatable")
    parser.add_argument("--target", required=True, help="stable logical node name")
    parser.add_argument("--message", required=True)
    parser.add_argument("--from-name", default=socket.gethostname())
    args = parser.parse_args()

    result = lookup([parse_endpoint(s) for s in args.seed], args.target)
    record = result["record"]
    delivery = rpc(
        (record["host"], int(record["port"])),
        {"action": "MESSAGE", "from": args.from_name, "text": args.message},
    )
    print(json.dumps({"lookup_path": result["path"], "record": record, "delivery": delivery}, indent=2))


if __name__ == "__main__":
    main()
