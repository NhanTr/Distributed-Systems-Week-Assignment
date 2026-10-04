import unittest

from bully_election_simulator import BullyCluster


class BullyElectionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = BullyCluster().run_failure_demo()
        cls.trace = "\n".join(cls.result.trace)

    def test_all_surviving_nodes_agree_on_p4(self) -> None:
        self.assertEqual(self.result.leader_views, {1: 4, 2: 4, 3: 4, 4: 4})

    def test_trace_contains_failure_detection_and_election(self) -> None:
        self.assertIn("[SYSTEM] Active Leader P5 terminated.", self.trace)
        self.assertIn("[P2] Timeout detected! Initiating Election...", self.trace)
        self.assertIn("[P2] Send ELECTION -> P3, P4, P5", self.trace)
        self.assertIn("[P4] No higher process replied -> NEW LEADER!", self.trace)

    def test_coordinator_is_announced_to_lower_nodes(self) -> None:
        self.assertIn(
            "[P4] Broadcast COORDINATOR victory to P1, P2, P3", self.trace
        )
        self.assertIn(
            "[SYSTEM] Verification passed: P1-P4 agree that P4 is leader.",
            self.trace,
        )


if __name__ == "__main__":
    unittest.main()
