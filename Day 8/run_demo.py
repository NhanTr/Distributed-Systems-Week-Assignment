"""Checked, real-process demonstration of the lecture's expected trace."""
import argparse
from pathlib import Path
import time
from cluster import Cluster
from gossip_quorum import Proxy, QuorumError, rpc


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='execution_trace.log')
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8', buffering=1) as trace:
        def emit(line):
            print(line, flush=True)
            trace.write(line + '\n')

        with Cluster(gossip_enabled=False, log_dir=output.parent / 'replica_logs') as cluster:
            emit('[SYSTEM] Five independent replica processes, TCP localhost:')
            for name, process in cluster.processes.items():
                emit(f'  {name}: PID={process.pid}, address={cluster.peers[name]}')
            proxy = Proxy(cluster.peers, nr=3, nw=3, emit=emit)

            def state(name):
                return rpc(cluster.peers[name], {'op': 'snapshot'})['state']

            def sync(source, dest):
                rpc(cluster.peers[source], {'op': 'sync', 'peer': dest})
                emit(f'[GOSSIP] {source} initiates Push-Pull with {dest}; {dest} state={state(dest)}')

            require(proxy.write('user1', 'Bob') == 1, 'initial version')
            require('user1' not in state('S4') and 'user1' not in state('S5'), 'expected stale replicas')
            emit('[SYSTEM] S4 and S5 are stale (Ver 0 / absent).')
            require(proxy.read('user1', nodes=['S3', 'S4', 'S5']) == 'Bob', 'highest version read')
            sync('S3', 'S4')
            sync('S4', 'S5')
            require(all(state(n)['user1']['value'] == 'Bob' for n in cluster.peers), 'live convergence')

            require(proxy.delete('user1') == 2, 'delete version')
            require(state('S5')['user1']['value'] == 'Bob', 'S5 must hold old live record')
            emit('[SYSTEM] S5 still holds Bob Ver 1. Exchange its old record with tombstone Ver 2:')
            sync('S5', 'S1')
            require(state('S1')['user1']['is_tombstone'], 'old data resurrected at S1')
            require(state('S5')['user1']['is_tombstone'], 'S5 did not learn tombstone')
            require(proxy.read('user1') is None, 'deleted read')

            emit('[SYSTEM] Enable random peer Push-Pull every 2 seconds; wait for all five tombstones.')
            for address in cluster.peers.values():
                rpc(address, {'op': 'gossip', 'enabled': True})
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                states = {n: state(n) for n in cluster.peers}
                if all(s['user1']['is_tombstone'] and s['user1']['version_number'] == 2 for s in states.values()):
                    break
                time.sleep(.1)
            else:
                raise AssertionError('background tombstone convergence deadline exceeded')
            for name, memory in states.items():
                emit(f'[{name}] Memory State -> {memory}')
            emit('[SYSTEM] All nodes successfully converged via Tombstone gossip.')

            cluster.stop('S1')
            cluster.stop('S2')
            emit('[SYSTEM] Stop S1 and S2: three replicas remain, NR=NW=3 still works.')
            require(proxy.write('survivor', 'available') == 3, 'version after delete')
            require(proxy.read('survivor') == 'available', 'two failure availability')
            cluster.stop('S3')
            emit('[SYSTEM] Stop S3: only two replicas remain; read and write must fail explicitly.')
            for label, action in [('READ', lambda: proxy.read('survivor')),
                                  ('WRITE', lambda: proxy.write('survivor', 'rejected'))]:
                try:
                    action()
                except QuorumError as error:
                    emit(f'[EXPECTED {label} FAILURE] {error}')
                else:
                    raise AssertionError(f'{label} unexpectedly succeeded without quorum')
            emit('[PASS] All demo checks passed. Replica protocol logs: replica_logs/S1.log ... S5.log')
        emit('[SYSTEM] All replica processes stopped.')


if __name__ == '__main__':
    main()
