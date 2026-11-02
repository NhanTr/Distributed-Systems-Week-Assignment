"""Interactive terminal for a five-process TCP key-value cluster."""
import argparse
import json
import shlex
from cluster import Cluster
from gossip_quorum import Proxy, rpc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nr', type=int, default=3)
    parser.add_argument('--nw', type=int, default=3)
    args = parser.parse_args()
    # Validate before allocating resources.
    from gossip_quorum import validate_quorums
    validate_quorums(5, args.nr, args.nw)
    with Cluster(log_dir='interactive_logs') as cluster:
        proxy = Proxy(cluster.peers, args.nr, args.nw, emit=print)
        print('Commands: write KEY VALUE | read KEY | delete KEY | state | sync S1 S5 | stop S5 | quit')
        while True:
            try:
                parts = shlex.split(input('lab> '))
                if not parts:
                    continue
                command, *params = parts
                if command in ('quit', 'exit'):
                    break
                if command == 'write' and len(params) == 2:
                    print('version:', proxy.write(*params))
                elif command == 'read' and len(params) == 1:
                    print('value:', repr(proxy.read(*params)))
                elif command == 'delete' and len(params) == 1:
                    print('tombstone version:', proxy.delete(*params))
                elif command == 'state' and not params:
                    for node, address in cluster.peers.items():
                        try:
                            print(node, json.dumps(rpc(address, {'op': 'snapshot'})['state'], ensure_ascii=False))
                        except OSError:
                            print(node, 'offline')
                elif command == 'sync' and len(params) == 2:
                    print(rpc(cluster.peers[params[0]], {'op': 'sync', 'peer': params[1]}))
                elif command == 'stop' and len(params) == 1:
                    cluster.stop(params[0])
                else:
                    print('Unknown command or invalid argument count')
            except (EOFError, KeyboardInterrupt):
                break
            except (ValueError, RuntimeError, OSError, KeyError) as error:
                print('ERROR:', error)


if __name__ == '__main__':
    main()
