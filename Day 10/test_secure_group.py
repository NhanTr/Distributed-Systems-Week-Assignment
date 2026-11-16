"""Behavioral checks for the Day 10 lab (no external services)."""
import copy
import secrets
import multiprocessing as mp
import socket
import struct
import subprocess
import sys
from pathlib import Path
import unittest
from datetime import time

from cryptography.exceptions import InvalidTag
from secure_group import Controller, MemberState, allowed, open_box, seal, recover
import secure_group as sg


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.group = Controller(8)
        self.states = {}
        self.history = {}

    def join(self, name):
        update, bootstrap = self.group.join(name)
        for state in self.states.values():
            state.apply(update)
        state = MemberState(bootstrap)
        self.states[name] = state
        # Model a malicious member retaining every version it ever learned.
        for member, current in self.states.items():
            self.history.setdefault(member, {}).update(
                {(node, v): key for node, (v, key) in current.keys.items()})
        return state, update

    def test_join_excludes_past_and_updates_only_own_path(self):
        alice, _ = self.join('alice')
        past = self.group.encrypt('before join')
        bob, update = self.join('bob')
        with self.assertRaises((InvalidTag, ValueError)):
            bob.decrypt(past)
        with self.assertRaises(InvalidTag):
            open_box(bob.keys[1][1], past['box'], past['header'])
        current = self.group.encrypt('current')
        for state in (alice, bob):
            self.assertEqual(state.decrypt(current), 'current')
            self.assertEqual(len(state.keys), 4)
            self.assertEqual(set(state.keys), set(state.path))
        self.assertLessEqual(len(update['blocks']), 3)

    def test_departed_member_cannot_recover_keys_even_with_all_old_keys(self):
        for i in range(8):
            self.join(str(i))
        departed = self.states.pop('3')
        stolen = dict(self.history['3'])
        self.assertGreater(len(stolen), 4)  # Includes keys from preceding join epochs.
        update = self.group.leave('3')
        self.assertEqual(len(update['blocks']), 5)
        recovered = recover(stolen, update)
        self.assertEqual(len(recovered), len(stolen))
        self.assertNotIn((1, self.group.epoch), recovered)
        survivor = self.states['0']
        surviving_keys = {(node, v): k for node, (v, k) in survivor.keys.items()}
        self.assertIn((1, self.group.epoch), recover(surviving_keys, update))
        future = self.group.encrypt('survivors only')
        with self.assertRaises((InvalidTag, ValueError)):
            departed.decrypt(future)
        with self.assertRaises(InvalidTag):
            open_box(departed.keys[1][1], future['box'], future['header'])
        for state in self.states.values():
            state.apply(update)
            self.assertEqual(state.decrypt(future), 'survivors only')

    def test_rekey_replay_and_tamper_leave_client_state_unchanged(self):
        alice, _ = self.join('alice')
        update, _ = self.group.join('bob')
        damaged = copy.deepcopy(update)
        damaged['blocks'][0]['box']['ciphertext'] = '00' * 48
        before = copy.deepcopy(alice.keys)
        with self.assertRaises((InvalidTag, ValueError)):
            alice.apply(damaged)
        self.assertEqual(alice.keys, before)
        alice.apply(update)
        with self.assertRaises(ValueError):
            alice.apply(update)

    def test_membership_validation_and_reused_leaf(self):
        self.join('a')
        with self.assertRaises(ValueError):
            self.group.join('a')
        with self.assertRaises(ValueError):
            self.group.leave('missing')
        departed = self.states.pop('a')
        self.group.leave('a')
        new, _ = self.join('a')
        with self.assertRaises((InvalidTag, ValueError)):
            departed.decrypt(self.group.encrypt('new incarnation'))
        self.assertEqual(new.decrypt(self.group.encrypt('hello')), 'hello')
        for i in range(7):
            self.join(str(i))
        epoch = self.group.epoch
        with self.assertRaises(ValueError):
            self.group.join('overflow')
        self.assertEqual(self.group.epoch, epoch)
        for n in (0, 1, 3, 7):
            with self.assertRaises(ValueError):
                Controller(n)

    def test_leave_block_counts_at_several_heights(self):
        for capacity, expected in ((2, 1), (4, 3), (8, 5), (16, 7), (32, 9)):
            group = Controller(capacity)
            for i in range(capacity):
                group.join(str(i))
            self.assertEqual(len(group.leave('0')['blocks']), expected)

    def test_ciphertext_and_authenticated_header_tampering(self):
        key = secrets.token_bytes(32)
        header = {'kind': 'payload', 'epoch': 4}
        box = seal(key, b'hello', header)
        self.assertEqual(open_box(key, box, header), b'hello')
        with self.assertRaises(InvalidTag):
            open_box(key, box, {'kind': 'payload', 'epoch': 5})
        box['ciphertext'] = '00' * 21
        with self.assertRaises(InvalidTag):
            open_box(key, box, header)

    def test_policy_boundaries_and_default_deny(self):
        for moment, want in ((time(7, 59, 59), False), (time(8), True),
                             (time(12), True), (time(18), True),
                             (time(18, 0, 0, 1), False)):
            self.assertEqual(allowed(True, 'Publisher', 'publish', moment), want)
        for active, role, action, moment in (
            (False, 'Publisher', 'publish', time(12)),
            (True, 'Subscriber', 'publish', time(12)),
            (True, 'Publisher', 'delete', time(12)),
            (True, None, 'publish', time(12)),
            (True, 'Publisher', [], time(12)),
            (True, 'Publisher', 'publish', '12:00'),
        ):
            self.assertFalse(allowed(active, role, action, moment))


class SocketTests(unittest.TestCase):
    fixed_time = '12:00:00'

    def setUp(self):
        self.context = mp.get_context('spawn')
        self.registry = {name: {'secret': secrets.token_bytes(32), 'role': role}
                         for name, role in (('alice', 'Publisher'), ('bob', 'Subscriber'))}
        self.stop = self.context.Event()
        parent, child = self.context.Pipe(False)
        self.server = self.context.Process(target=sg.serve,
            args=(self.registry, child, self.stop, self.fixed_time))
        self.server.start()
        child.close()
        self.addCleanup(self.cleanup)
        self.assertTrue(parent.poll(10), 'server startup timeout')
        self.port = parent.recv()
        parent.close()
        self.clients = []

    def cleanup(self):
        for client in getattr(self, 'clients', []):
            client.close()
        self.stop.set()
        self.server.join(5)
        if self.server.is_alive():
            self.server.terminate()
            self.server.join()
            self.fail('server failed to stop cleanly')
        self.assertEqual(self.server.exitcode, 0)

    def connect(self, name, secret=None):
        client = sg.Client(name, secret or self.registry[name]['secret'], self.port)
        self.clients.append(client)
        return client

    def test_tcp_auth_membership_publication_and_revocation(self):
        alice = self.connect('alice')
        with self.assertRaises(ValueError):
            self.connect('alice')
        with self.assertRaises((ValueError, EOFError)):
            self.connect('bob', secrets.token_bytes(32))
        bob = self.connect('bob')
        self.assertEqual(bob.request('publish', text='forbidden', role='Publisher')['error'], 'DENIED')
        self.assertEqual(alice.request('delete')['error'], 'DENIED')
        self.assertTrue(alice.request('publish', text='hello')['ok'])
        bob.request('sync')
        self.assertEqual(bob.messages[-1], 'hello')
        self.assertEqual(alice.messages[-1], 'hello')
        self.assertTrue(bob.request('leave')['ok'])
        self.assertEqual(bob.request('publish', text='inactive')['error'], 'DENIED')
        self.assertTrue(alice.request('publish', text='after leave')['ok'])
        self.assertEqual(bob.messages, ['hello'])

    def test_bounded_frames_and_session_replay(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        left.sendall(struct.pack('!I', sg.MAX_FRAME + 1))
        with self.assertRaises(ValueError):
            sg.receive(right)
        key = secrets.token_bytes(32)
        sender = sg.Channel(left, key, 'c2s', 's2c')
        receiver = sg.Channel(right, key, 's2c', 'c2s')
        sender.send({'op': 'sync'})
        packet = sg.receive(right)
        sg.send(left, packet)
        self.assertEqual(receiver.receive(), {'op': 'sync'})
        sg.send(left, packet)
        with self.assertRaises(ValueError):
            receiver.receive()


class NightSocketTests(SocketTests):
    fixed_time = '20:00:00'

    # Reuse the fixture and framing test; the noon publication scenario is
    # replaced with a time-spoof attempt against an actual nighttime server.
    def test_tcp_auth_membership_publication_and_revocation(self):
        alice = self.connect('alice')
        result = alice.request('publish', text='night', time='12:00:00', role='Publisher')
        self.assertEqual(result.get('error'), 'DENIED')
        self.assertEqual(alice.messages, [])


class DemoTests(unittest.TestCase):
    def test_separate_process_demo_completes_without_forced_shutdown(self):
        result = subprocess.run([sys.executable, 'run_demo.py', '--no-trace'],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ALL CHECKS PASSED', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
