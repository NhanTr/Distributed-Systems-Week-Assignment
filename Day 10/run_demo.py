"""Repeatable demo: one server process and eight independent TCP clients."""
import argparse
import copy
import multiprocessing as mp
import os
from pathlib import Path
import secrets
import select
from datetime import time

from cryptography.exceptions import InvalidTag
from secure_group import Client, Controller, MemberState, allowed, open_box, recover, serve


def require(condition, detail='verification failed'):
    if not condition:
        raise AssertionError(detail)


def rejected(operation, exceptions=(InvalidTag, ValueError)):
    try:
        operation()
    except exceptions:
        return
    raise AssertionError('operation unexpectedly succeeded')


def client_process(identity, secret, port, control):
    client = None
    try:
        client = Client(identity, secret, port)
        control.send({'pid': os.getpid()})
        while True:
            if control.poll(0.02):
                command = control.recv()
                if command['op'] == 'close':
                    return
                if command['op'] == 'inspect':
                    # Proof fixture only: export THIS client's keys to the trusted
                    # demo launcher. No other client gets this snapshot.
                    result = {'epoch': client.state.epoch, 'leaf': client.state.path[0],
                              'keys': {str(n): [v, k.hex()] for n, (v, k) in client.state.keys.items()},
                              'messages': list(client.messages)}
                else:
                    result = client.request(**command)
                control.send(result)
            elif select.select([client.sock], [], [], 0)[0]:
                client.process(client.channel.receive())
    finally:
        if client:
            client.close()
        control.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-trace', action='store_true', help='do not write execution_trace.log')
    args = parser.parse_args()
    trace = None if args.no_trace else (Path(__file__).parent / 'execution_trace.log').open('w')

    def log(message):
        print(message, flush=True)
        if trace:
            trace.write(message + '\n')
            trace.flush()

    context = mp.get_context('spawn')
    stop = context.Event()
    processes, controls = [], {}
    registry = {str(i): {'secret': secrets.token_bytes(32),
                        'role': 'Publisher' if i == 0 else 'Subscriber'} for i in range(8)}

    def reply(pipe):
        require(pipe.poll(10), 'process response timeout')
        return pipe.recv()

    def call(identity, op, **attributes):
        pipe = controls[identity]
        pipe.send({'op': op, **attributes})
        return reply(pipe)

    def start_client(identity):
        parent, child = context.Pipe()
        process = context.Process(target=client_process,
            args=(identity, registry[identity]['secret'], port, child), name='CLIENT_' + identity)
        process.start()
        processes.append(process)
        child.close()
        controls[identity] = parent
        log('[CLIENT_{}] PID={} authenticated; joined with only its path keys.'.format(identity, reply(parent)['pid']))

    try:
        parent, child = context.Pipe(False)
        server = context.Process(target=serve, args=(registry, child, stop, '12:00:00'), name='KEY_SERVER')
        server.start()
        processes.append(server)
        child.close()
        try:
            port = reply(parent)
        finally:
            parent.close()
        log('[KEY_SERVER] PID={} localhost TCP port={}; capacity=8, height=3.'.format(server.pid, port))
        log('[CLOCK] Demo injects server-local 12:00:00; normal serve() uses actual local time.')
        for i in range(7):
            start_client(str(i))
        before = call('0', 'publish', text='Before entrant joins')
        require(before.get('ok'))
        start_client('7')
        entrant = MemberState(call('7', 'inspect'))
        rejected(lambda: open_box(entrant.keys[1][1], before['envelope']['box'], before['envelope']['header']))
        log('[EXCLUSION] Entrant 7 cannot decrypt recorded past ciphertext: AES-GCM InvalidTag.')
        current = call('0', 'publish', text='All eight receive this')
        require(current.get('ok'))
        for i in range(8):
            call(str(i), 'sync')
            snapshot = call(str(i), 'inspect')
            require(snapshot['messages'][-1] == 'All eight receive this')
            require(len(snapshot['keys']) == 4)
        log('[MULTICAST] One AES-256-GCM application envelope; all 8 independent clients decrypted it.')
        old = MemberState(call('7', 'inspect'))
        survivor_old = MemberState(call('0', 'inspect'))
        leave = call('7', 'leave')
        require(leave.get('ok'))
        update = leave['update']
        require(len(update['blocks']) == 5)
        log('[LEAVE] Member 7 revoked; 5 encrypted blocks, lecture upper bound 2h=6; 7 TCP rekey deliveries.')
        for block in update['blocks']:
            log('[REKEY] node={} <- child={} version={} epoch={}'.format(
                block['node'], block['wrapper'], block['version'], update['epoch']))
        stolen = {(n, v): k for n, (v, k) in old.keys.items()}
        learned = recover(stolen, update)
        require(len(learned) == len(stolen), 'departed client recovered a replacement key')
        after = call('0', 'publish', text='Survivors only')
        require(after.get('ok'))
        rejected(lambda: open_box(old.keys[1][1], after['envelope']['box'], after['envelope']['header']))
        log('[EXCLUSION] Departed client retained ALL old keys: recovered no new keys; future ciphertext InvalidTag.')
        for i in range(7):
            call(str(i), 'sync')
            require(call(str(i), 'inspect')['messages'][-1] == 'Survivors only')
        require(call('7', 'inspect')['messages'][-1] == 'All eight receive this')
        log('[SURVIVORS] All 7 remaining clients decrypted the new payload.')
        require(call('1', 'publish', text='forbidden', role='Publisher', time='12:00')['error'] == 'DENIED')
        require(call('7', 'publish', text='inactive')['error'] == 'DENIED')
        require(call('0', 'delete')['error'] == 'DENIED')
        log('[ABAC] Subscriber role spoof, inactive member, unsupported action: DENIED over TCP.')
        for moment, expected in ((time(7, 59, 59), False), (time(8), True),
                                 (time(18), True), (time(18, 0, 0, 1), False)):
            require(allowed(True, 'Publisher', 'publish', moment) is expected)
            log('[ABAC] Policy boundary {} -> {}.'.format(moment, 'ALLOW' if expected else 'DENY'))
        survivor_old.apply(update)
        rejected(lambda: survivor_old.apply(update))
        damaged = copy.deepcopy(after['envelope'])
        damaged['box']['ciphertext'] = '00' * 40
        rejected(lambda: survivor_old.decrypt(damaged))
        changed_header = dict(after['envelope']['header'], node=2)
        rejected(lambda: open_box(survivor_old.keys[1][1], after['envelope']['box'], changed_header))
        log('[INTEGRITY] Modified ciphertext, modified authenticated header, stale rekey epoch: rejected.')
        for capacity, expected in ((2, 1), (4, 3), (8, 5), (16, 7), (32, 9)):
            group = Controller(capacity)
            for i in range(capacity):
                group.join(str(i))
            count = len(group.leave('0')['blocks'])
            require(count == expected)
            log('[SCALING] capacity={} leave blocks={} = 2h-1.'.format(capacity, count))
    finally:
        for pipe in controls.values():
            try:
                pipe.send({'op': 'close'})
            except (OSError, EOFError):
                pass
        forced = []
        for process in processes[1:]:
            process.join(5)
            if process.is_alive():
                forced.append(process.name)
                process.terminate()
                process.join()
        stop.set()
        if processes:
            processes[0].join(5)
            if processes[0].is_alive():
                forced.append('KEY_SERVER')
                processes[0].terminate()
                processes[0].join()
        for pipe in controls.values():
            pipe.close()
        if trace:
            trace.flush()
        require(not forced, 'forced shutdown: ' + ', '.join(forced))
        require(all(p.exitcode == 0 for p in processes), 'child process failed')
        log('[CLEANUP] All 9 processes exited normally; sockets closed.')
    log('[RESULT] ALL CHECKS PASSED')
    if trace:
        trace.close()


if __name__ == '__main__':
    main()
