import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from timesignalfx_daily import load_trades, post_text, summarize


class DailySummaryTests(unittest.TestCase):
    def setUp(self):
        self.trades = load_trades(
            ROOT / "fixtures" / "sample_history.csv", "2026091", "USDJPY"
        )

    def test_daily_and_monthly_summary(self):
        result = summarize(self.trades, date(2026, 9, 2))
        self.assertEqual(result.trades, 1)
        self.assertEqual((result.wins, result.losses, result.draws), (1, 0, 0))
        self.assertAlmostEqual(result.daily_pips, 20.0)
        self.assertEqual(result.daily_net_jpy, 2950)
        self.assertEqual(result.monthly_trades, 3)
        self.assertEqual((result.monthly_wins, result.monthly_losses), (2, 1))
        self.assertAlmostEqual(result.monthly_pips, 35.0)
        self.assertEqual(result.monthly_net_jpy, 5200)

    def test_other_magic_is_excluded(self):
        self.assertEqual(len(self.trades), 3)

    def test_post_text_contains_core_metrics(self):
        text = post_text(summarize(self.trades, date(2026, 9, 2)), "TimeSignalFX")
        self.assertIn("1勝0敗", text)
        self.assertIn("+20.0 pips", text)
        self.assertIn("+2,950円", text)
        self.assertIn("+5,200円", text)


if __name__ == "__main__":
    unittest.main()

