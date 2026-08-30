import sys
import unittest
import urllib.parse
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from timesignalfx_daily import (
    DEFAULT_POST_SITE_URL,
    Summary,
    Trade,
    build_payload,
    expected_site_strings,
    load_post_state,
    post_text,
    posted_dates,
    record_posted,
    summarize,
    x_buffer_weighted_length,
)


class DailySummaryTests(unittest.TestCase):
    def setUp(self):
        self.trades = [
            Trade("sample-1", "sample", "USDJPY", "BUY", datetime(2026, 9, 1, 9),
                  Decimal("150.000"), Decimal("150.250"), Decimal("3750")),
            Trade("sample-2", "sample", "USDJPY", "SELL", datetime(2026, 9, 1, 11),
                  Decimal("150.200"), Decimal("150.300"), Decimal("-1500")),
            Trade("sample-3", "sample", "USDJPY", "SELL", datetime(2026, 9, 2, 9),
                  Decimal("150.500"), Decimal("150.300"), Decimal("2950")),
        ]

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

    def test_post_text_contains_core_metrics(self):
        text = post_text(summarize(self.trades, date(2026, 9, 2)), "TimeSignalFX")
        self.assertIn("1勝0敗", text)
        self.assertIn("+20.0 pips", text)
        self.assertIn("+2,950円", text)
        self.assertIn("+5,200円", text)
        self.assertIn("実績👇", text)
        self.assertIn("utm_source=x&utm_medium=social&utm_campaign=daily_result", text)
        self.assertLessEqual(x_buffer_weighted_length(text), 280)

    def test_site_parity_fields_include_day_and_month_values(self):
        result = summarize(self.trades, date(2026, 9, 2))
        expected = expected_site_strings(result)
        self.assertIn("2026-09-02", expected)
        self.assertIn("+2,950円", expected)
        self.assertIn("+5,200円", expected)
        self.assertIn("+35.0 pips", expected)

    def test_buffer_payload_is_dry_run_and_gated(self):
        payload = build_payload("text", "https://example.com/a.png", False, "site_values_mismatch")
        self.assertTrue(payload["dry_run"])
        self.assertFalse(payload["eligible_to_post"])
        self.assertEqual("site_values_mismatch", payload["gate_reason"])
        self.assertIn("createPost", payload["request_body"]["query"])

    def test_public_site_url_path_is_ascii_encodable(self):
        url = "https://timesignalfx.com/実績/"
        parts = urllib.parse.urlsplit(url)
        request_url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, urllib.parse.quote(parts.path), parts.query, parts.fragment)
        )
        self.assertEqual("https://timesignalfx.com/%E5%AE%9F%E7%B8%BE/", request_url)
        request_url.encode("ascii")

    def test_post_url_is_encoded_and_keeps_campaign(self):
        self.assertIn("/%E5%AE%9F%E7%B8%BE/", DEFAULT_POST_SITE_URL)
        self.assertIn("utm_campaign=daily_result", DEFAULT_POST_SITE_URL)
        DEFAULT_POST_SITE_URL.encode("ascii")

    def test_posted_state_is_unique_by_date_and_records_x_reference(self):
        with TemporaryDirectory() as directory:
            state = Path(directory) / "posted_dates.json"
            record_posted(
                state,
                date(2026, 8, 28),
                "buffer-1",
                x_url="https://x.com/TimeSignalFX/status/2094112843037618233",
                posted_at="2026-08-31T02:19:43+09:00",
            )
            record_posted(
                state,
                date(2026, 8, 28),
                "buffer-1",
                x_url="https://x.com/TimeSignalFX/status/2094112843037618233",
                posted_at="2026-08-31T02:19:43+09:00",
            )
            self.assertEqual({"2026-08-28"}, posted_dates(state))
            value = load_post_state(state)
            self.assertEqual(1, len(value["posts"]))
            self.assertEqual("2094112843037618233", value["posts"][0]["x_post_id"])


if __name__ == "__main__":
    unittest.main()
