import unittest
from concurrent.futures import ThreadPoolExecutor
from reference_core.budget import BudgetExceeded, BudgetLedger, sam_video_microusd


class BudgetTests(unittest.TestCase):
    def test_frame_minimum(self):
        self.assertEqual(sam_video_microusd(1), 10_000)

    def test_three_concepts(self):
        self.assertEqual(sam_video_microusd(900, 3), 540_000)

    def test_reservation_blocks_overspend(self):
        ledger = BudgetLedger(100, 10)
        ledger.reserve("a", 80)
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("b", 21)

    def test_settlement_releases_difference(self):
        ledger = BudgetLedger(100, 10)
        ledger.reserve("a", 80)
        ledger.settle("a", 30)
        ledger.reserve("b", 70)
        self.assertEqual(ledger.pending_microusd, 70)

    def test_unknown_cost_keeps_reservation(self):
        ledger = BudgetLedger(100, 10)
        ledger.reserve("a", 100)
        ledger.settle("a", None)
        self.assertEqual(ledger.charged_microusd, 100)
        self.assertIn("a", ledger.uncertain_calls)
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("b", 1)

    def test_retry_is_another_call(self):
        ledger = BudgetLedger(100, 1)
        ledger.reserve("a", 1)
        ledger.settle("a", 1)
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("retry-a", 1)

    def test_duplicate_call_rejected(self):
        ledger = BudgetLedger(100, 5)
        ledger.reserve("a", 1)
        with self.assertRaises(ValueError):
            ledger.reserve("a", 1)

    def test_negative_cost_rejected(self):
        ledger = BudgetLedger(100, 5)
        with self.assertRaises(ValueError):
            ledger.reserve("a", -1)

    def test_unsent_release(self):
        ledger = BudgetLedger(100, 5)
        ledger.reserve("a", 100)
        ledger.release_unsent("a")
        ledger.reserve("b", 100)

    def test_actual_overrun_is_recorded(self):
        ledger = BudgetLedger(100, 5)
        ledger.reserve("a", 80)
        with self.assertRaises(BudgetExceeded):
            ledger.settle("a", 101)
        self.assertEqual(ledger.charged_microusd, 101)

    def test_concurrent_reservations(self):
        ledger = BudgetLedger(100, 20)
        def attempt(number):
            try:
                ledger.reserve(str(number), 60)
                return True
            except BudgetExceeded:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            successes = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(successes), 1)
