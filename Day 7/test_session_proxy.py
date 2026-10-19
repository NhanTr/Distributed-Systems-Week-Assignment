"""Behavior tests for the lecture's three-replica session proxy lab."""

import unittest

from session_proxy import Cluster, SessionProxy, UnavailableError, dominates


class SessionProxyTests(unittest.TestCase):
    def setUp(self):
        self.cluster = Cluster(replication_delay=5)
        self.proxy = SessionProxy(self.cluster)

    def test_vector_dominance_is_componentwise(self):
        self.assertTrue(dominates((5, 3, 0), (4, 2, 0)))
        self.assertFalse(dominates((5, 1, 0), (4, 2, 0)))
        self.assertFalse(dominates((0, 2, 0), (1, 0, 0)))
        with self.assertRaises(ValueError):
            dominates((1, 0), (1, 0, 0))

    def test_write_ack_updates_only_issuing_session(self):
        self.proxy.write('alice', 'S1', 'profile', 'Alice')
        self.assertEqual(self.proxy.session('alice').v_write, (1, 0, 0))
        self.assertEqual(self.proxy.session('alice').v_read, (0, 0, 0))
        self.assertEqual(self.proxy.session('bob').v_write, (0, 0, 0))
        self.assertIsNone(self.cluster.raw_read('S2', 'profile'))

    def test_read_your_writes_repairs_before_background_delivery(self):
        self.proxy.write('alice', 'S1', 'profile', 'Alice')
        self.assertEqual(self.proxy.read('alice', 'S2', 'profile'), 'Alice')
        self.assertEqual(self.cluster.nodes['S2'].clock, (1, 0, 0))
        self.assertEqual(self.proxy.session('alice').v_read, (1, 0, 0))
        self.assertEqual(self.cluster.now, 0)

    def test_monotonic_reads_for_observer_with_no_writes(self):
        self.proxy.write('writer', 'S1', 'inbox', 'five emails')
        self.assertEqual(self.proxy.read('reader', 'S1', 'inbox'), 'five emails')
        self.assertEqual(self.proxy.session('reader').v_write, (0, 0, 0))
        self.assertIsNone(self.cluster.raw_read('S3', 'inbox'))
        self.assertEqual(self.proxy.read('reader', 'S3', 'inbox'), 'five emails')

    def test_background_delay_then_eventual_delivery(self):
        self.proxy.write('alice', 'S1', 'profile', 'Alice')
        self.cluster.advance(4)
        self.assertIsNone(self.cluster.raw_read('S3', 'profile'))
        self.cluster.advance(1)
        self.assertEqual(self.cluster.raw_read('S3', 'profile'), 'Alice')
        self.assertEqual(self.cluster.nodes['S3'].clock, (1, 0, 0))

    def test_read_repairs_union_of_read_and_write_contexts(self):
        self.proxy.write('author', 'S1', 'post', 'parent')
        self.proxy.read('reader', 'S1', 'post')
        self.proxy.write('reader', 'S2', 'reply', 'response')
        self.assertEqual(self.proxy.session('reader').v_write, (1, 1, 0))
        self.assertEqual(self.proxy.read('reader', 'S3', 'post'), 'parent')
        self.assertEqual(self.proxy.read('reader', 'S3', 'reply'), 'response')
        self.assertEqual(self.cluster.nodes['S3'].clock, (1, 1, 0))

    def test_unreachable_history_blocks_without_changing_session(self):
        self.proxy.write('alice', 'S1', 'profile', 'Alice')
        self.cluster.set_online('S1', False)
        with self.assertRaises(UnavailableError):
            self.proxy.read('alice', 'S2', 'profile')
        self.assertIsNone(self.cluster.raw_read('S2', 'profile'))
        self.assertEqual(self.proxy.session('alice').v_read, (0, 0, 0))
        self.cluster.set_online('S1', True)
        self.assertEqual(self.proxy.read('alice', 'S2', 'profile'), 'Alice')

    def test_fetches_from_backup_when_origin_is_offline(self):
        self.proxy.write('alice', 'S1', 'profile', 'Alice')
        self.proxy.read('alice', 'S2', 'profile')
        self.cluster.set_online('S1', False)
        self.assertEqual(self.proxy.read('alice', 'S3', 'profile'), 'Alice')

    def test_offline_target_rejects_read_and_write(self):
        self.cluster.set_online('S2', False)
        with self.assertRaises(UnavailableError):
            self.proxy.write('alice', 'S2', 'profile', 'Alice')
        with self.assertRaises(UnavailableError):
            self.proxy.read('alice', 'S2', 'profile')
        self.assertEqual(self.proxy.session('alice').v_write, (0, 0, 0))

    def test_out_of_order_delivery_buffers_dependent_write(self):
        first = self.proxy.write('alice', 'S1', 'profile', 'Alice')
        second = self.proxy.write('alice', 'S1', 'profile', 'Alice 2')
        node = self.cluster.nodes['S3']
        node.receive(second)
        self.assertEqual(node.clock, (0, 0, 0))
        self.assertNotIn('profile', node.values)
        node.receive(first)
        self.assertEqual(node.clock, (2, 0, 0))
        self.assertEqual(self.cluster.raw_read('S3', 'profile'), 'Alice 2')

    def test_duplicate_background_delivery_cannot_undo_repair(self):
        self.proxy.write('alice', 'S1', 'profile', 'Alice')
        self.proxy.write('alice', 'S1', 'profile', 'Alice 2')
        self.proxy.read('alice', 'S2', 'profile')
        self.cluster.advance(5)
        self.assertEqual(self.cluster.raw_read('S2', 'profile'), 'Alice 2')
        self.assertEqual(self.cluster.nodes['S2'].clock, (2, 0, 0))
        self.assertEqual(len(self.cluster.nodes['S2'].history), 2)

    def test_concurrent_updates_converge_independent_of_delivery_order(self):
        self.proxy.write('alice', 'S1', 'profile', 'A')
        self.proxy.write('bob', 'S2', 'profile', 'B')
        self.cluster.advance(5)
        self.assertEqual([self.cluster.raw_read(s, 'profile') for s in ('S1', 'S2', 'S3')],
                         ['B', 'B', 'B'])
        self.assertEqual(self.cluster.nodes['S3'].clock, (1, 1, 0))

    def test_delayed_delivery_retries_when_replica_returns(self):
        self.proxy.write('alice', 'S1', 'profile', 'Alice')
        self.cluster.set_online('S3', False)
        self.cluster.advance(5)
        self.assertEqual(self.cluster.nodes['S3'].clock, (0, 0, 0))
        self.cluster.set_online('S3', True)
        self.cluster.advance(1)
        self.assertEqual(self.cluster.raw_read('S3', 'profile'), 'Alice')

    def test_invalid_requests_are_rejected(self):
        with self.assertRaises(ValueError):
            Cluster(replication_delay=-1)
        with self.assertRaises(ValueError):
            self.cluster.advance(-1)
        with self.assertRaises(ValueError):
            self.proxy.read('alice', 'S4', 'profile')
        with self.assertRaises(ValueError):
            self.proxy.write('alice', 'S1', '', 'Alice')
        with self.assertRaises(ValueError):
            self.proxy.read('', 'S1', 'profile')


if __name__ == '__main__':
    unittest.main()
