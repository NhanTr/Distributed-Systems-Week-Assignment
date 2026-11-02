import time
import unittest
from gossip_quorum import QuorumError, Record, Store, validate_quorums, Proxy, rpc
from cluster import Cluster


class StateTests(unittest.TestCase):
    def test_quorum_rules(self):
        validate_quorums(5, 3, 3)
        validate_quorums(5, 1, 5)
        for r, w in [(2, 3), (4, 2), (0, 5), (6, 3)]:
            with self.subTest(r=r, w=w), self.assertRaises(ValueError):
                validate_quorums(5, r, w)

    def test_tombstone_blocks_resurrection_and_allows_recreate(self):
        s = Store()
        s.merge({'x': Record(None, 2, True).wire()})
        s.merge({'x': Record('old', 1).wire()})
        self.assertTrue(s.snapshot()['x']['is_tombstone'])
        s.merge({'x': Record('new', 3).wire()})
        self.assertEqual(s.snapshot()['x']['value'], 'new')

    def test_equal_version_conflict_is_rejected(self):
        s = Store()
        s.merge({'x': Record('a', 1).wire()})
        with self.assertRaises(ValueError):
            s.merge({'x': Record('b', 1).wire()})


class SocketTests(unittest.TestCase):
    def setUp(self):
        self.cluster = Cluster(gossip_enabled=False)
        self.cluster.start()
        self.addCleanup(self.cluster.close)
        self.p = Proxy(self.cluster.peers, nr=3, nw=3)

    def state(self, node):
        return rpc(self.cluster.peers[node], {'op': 'snapshot'})['state']

    def test_highest_version_not_majority_value(self):
        self.assertEqual(self.p.write('x', 'Bob'), 1)
        self.assertNotIn('x', self.state('S5'))
        self.assertEqual(self.p.read('x', nodes=['S3', 'S4', 'S5']), 'Bob')
        self.assertIsNone(self.p.read('absent'))

    def test_push_pull_moves_updates_in_both_directions(self):
        rpc(self.cluster.peers['S1'], {'op': 'put', 'key': 'a', 'record': Record('A', 1).wire()})
        rpc(self.cluster.peers['S2'], {'op': 'put', 'key': 'b', 'record': Record('B', 2).wire()})
        rpc(self.cluster.peers['S1'], {'op': 'sync', 'peer': 'S2'})
        self.assertEqual(self.state('S1'), self.state('S2'))
        self.assertEqual(self.state('S1')['b']['value'], 'B')
        self.assertEqual(self.state('S2')['a']['value'], 'A')

    def test_delete_stale_rejoin_and_recreate(self):
        self.p.write('x', 'Bob')
        rpc(self.cluster.peers['S1'], {'op': 'sync', 'peer': 'S5'})
        self.assertEqual(self.p.delete('x'), 2)
        self.assertIsNone(self.p.read('x', nodes=['S3', 'S4', 'S5']))
        rpc(self.cluster.peers['S5'], {'op': 'sync', 'peer': 'S1'})
        self.assertTrue(self.state('S5')['x']['is_tombstone'])
        self.assertTrue(self.state('S1')['x']['is_tombstone'])
        self.assertEqual(self.p.write('x', ''), 3)
        self.assertEqual(self.p.read('x'), '')

    def test_quorum_with_two_failures_and_reject_three(self):
        self.cluster.stop('S1')
        self.cluster.stop('S2')
        self.p.write('x', 'available')
        self.assertEqual(self.p.read('x'), 'available')
        self.cluster.stop('S3')
        with self.assertRaises(QuorumError):
            self.p.read('x')
        with self.assertRaises(QuorumError):
            self.p.write('x', 'cannot commit')

    def test_invalid_read_targets_do_not_fake_votes(self):
        with self.assertRaises(ValueError):
            self.p.read('x', nodes=['S1', 'S1', 'S2'])

    def test_background_gossip_converges_tombstones(self):
        self.p.write('x', 'Bob')
        self.p.delete('x')
        for address in self.cluster.peers.values():
            rpc(address, {'op': 'gossip', 'enabled': True})
        deadline = time.monotonic() + 16
        while time.monotonic() < deadline:
            states = [self.state(n) for n in self.cluster.peers]
            if all(s.get('x', {}).get('is_tombstone') for s in states):
                break
            time.sleep(.1)
        else:
            self.fail('Periodic gossip failed to converge within 16 seconds')
        self.assertEqual(len(set(p.pid for p in self.cluster.processes.values())), 5)


if __name__ == '__main__':
    unittest.main()
