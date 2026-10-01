import unittest

from logical_clock_simulator import vector_le, vectors_concurrent


class VectorClockTests(unittest.TestCase):
    def test_componentwise_order(self):
        self.assertTrue(vector_le([2, 1, 3], [3, 1, 4]))
        self.assertFalse(vector_le([2, 1, 3], [2, 2, 2]))

    def test_concurrency(self):
        self.assertTrue(vectors_concurrent([2, 1, 3], [2, 2, 2]))
        self.assertFalse(vectors_concurrent([2, 1, 3], [3, 1, 4]))
        self.assertFalse(vectors_concurrent([1, 1, 1], [1, 1, 1]))

    def test_mismatched_vector_sizes_are_rejected(self):
        with self.assertRaises(ValueError):
            vector_le([1, 2], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
