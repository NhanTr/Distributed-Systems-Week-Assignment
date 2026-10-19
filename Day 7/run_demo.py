#!/usr/bin/env python3
"""Run reproducible lab scenarios and optionally save their verified trace."""

from __future__ import annotations

import argparse
from pathlib import Path

from session_proxy import Cluster, SessionProxy, UnavailableError


def run_demo() -> str:
    lines: list[str] = []
    cluster = Cluster(replication_delay=5, emit=lines.append)
    proxy = SessionProxy(cluster)

    lines.append('=== Scenario 1: Read-Your-Writes (lecture sample) ===')
    lines.append("[CLIENT] Write W1 (Profile Name = 'Alice') -> S1")
    proxy.write('alice', 'S1', 'profile', 'Alice')
    assert proxy.session('alice').v_write == (1, 0, 0)
    assert cluster.raw_read('S2', 'profile') is None
    lines.append(f'[SYSTEM] Routing read R1 to S2; clock={cluster.nodes["S2"].clock}')
    assert proxy.read('alice', 'S2', 'profile') == 'Alice'
    assert cluster.now == 0
    lines.append('[CLIENT] PASS: Read-Your-Writes preserved before background delivery')

    lines.append('\n=== Scenario 2: Monotonic Reads (reader has issued no writes) ===')
    assert proxy.read('observer', 'S1', 'profile') == 'Alice'
    assert proxy.session('observer').v_write == (0, 0, 0)
    lines.append(f'[SYSTEM] Routing observer to S3; clock={cluster.nodes["S3"].clock}')
    assert cluster.raw_read('S3', 'profile') is None
    assert proxy.read('observer', 'S3', 'profile') == 'Alice'
    lines.append('[CLIENT] PASS: Monotonic Reads preserved on replica switch')

    lines.append('\n=== Scenario 3: Network delay and eventual propagation ===')
    proxy.write('alice', 'S1', 'profile', 'Alice Updated')
    cluster.advance(4)
    assert cluster.raw_read('S2', 'profile') == 'Alice'
    lines.append('[SYSTEM t=4] S2 still returns old value on diagnostic raw read')
    cluster.advance(1)
    assert all(cluster.raw_read(s, 'profile') == 'Alice Updated' for s in cluster.nodes)
    lines.append('[SYSTEM t=5] PASS: All three replicas converged to Alice Updated')

    lines.append('\n=== Scenario 4: Missing history blocks, recovery permits retry ===')
    isolated = Cluster(replication_delay=5, emit=lines.append)
    recovering = SessionProxy(isolated)
    recovering.write('alice', 'S1', 'profile', 'Alice')
    isolated.set_online('S1', False)
    try:
        recovering.read('alice', 'S2', 'profile')
    except UnavailableError as error:
        lines.append(f'[CLIENT] Expected blocked read: {error}')
    else:
        raise AssertionError('Unavailable history must prevent a stale response')
    assert recovering.session('alice').v_read == (0, 0, 0)
    isolated.set_online('S1', True)
    assert recovering.read('alice', 'S2', 'profile') == 'Alice'
    lines.append('[CLIENT] PASS: Retry repaired S2 after S1 became reachable')
    lines.append('\nAll four scenarios passed.')
    return '\n'.join(lines) + '\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional UTF-8 execution trace path')
    args = parser.parse_args()
    trace = run_demo()
    print(trace, end='')
    if args.output:
        args.output.write_text(trace, encoding='utf-8')


if __name__ == '__main__':
    main()
