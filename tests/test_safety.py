import unittest

from tools.safety import SafetyManager


class TestSafetyLimits(unittest.TestCase):
    def test_maximum_iterations_is_enforced(self):
        safety = SafetyManager(max_steps=2)
        self.assertEqual(safety.next_step(), 1)
        self.assertEqual(safety.next_step(), 2)
        with self.assertRaisesRegex(RuntimeError, "Maximum iteration"):
            safety.next_step()

    def test_measurable_cost_budget_is_enforced(self):
        safety = SafetyManager(max_cost=0.05)
        safety.add_cost(0.03)
        with self.assertRaisesRegex(RuntimeError, "Maximum cost"):
            safety.add_cost(0.03)


if __name__ == "__main__":
    unittest.main()
