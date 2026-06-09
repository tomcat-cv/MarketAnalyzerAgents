from pathlib import Path
import tempfile
import unittest

from marketanalyzeragents.intraday import build_suggestion, fetch_yahoo_market_data, yahoo_symbol
from marketanalyzeragents.agent_debate import DebateTurn
from marketanalyzeragents.portfolio_store import PortfolioStore, Quote
from marketanalyzeragents.review import build_daily_review


class PortfolioWorkflowTests(unittest.TestCase):
    def test_market_symbols_are_normalized_for_both_markets(self) -> None:
        self.assertEqual(yahoo_symbol("a_share", "600519"), "600519.SS")
        self.assertEqual(yahoo_symbol("a_share", "000001"), "000001.SZ")
        self.assertEqual(yahoo_symbol("a_share", "830799"), "830799.BJ")
        self.assertEqual(yahoo_symbol("us_equities", "nvda"), "NVDA")

    def test_external_history_drives_metrics_and_is_persisted(self) -> None:
        class Client:
            def get_json(self, url):
                if "interval=1m" in url:
                    timestamps = [1_780_998_000, 1_780_998_060]
                    closes = [101, 102]
                else:
                    timestamps = [1_780_819_200, 1_780_905_600, 1_780_992_000]
                    closes = [100, 90, 110]
                return {
                    "chart": {
                        "result": [{
                            "timestamp": timestamps,
                            "indicators": {"quote": [{
                                "open": closes,
                                "high": [value + 1 for value in closes],
                                "low": [value - 1 for value in closes],
                                "close": closes,
                                "volume": [1000] * len(closes),
                            }]},
                        }],
                        "error": None,
                    }
                }

        data = fetch_yahoo_market_data(Client(), "a_share", "600519")
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(Path(tmp) / "state.db")
            store.save_quotes([data.quote])
            store.save_price_bars(data.history)
            rows = store.recent_price_bars("a_share", "600519", "1d")

        self.assertEqual(data.quote.price, 102)
        self.assertEqual(data.quote.previous_close, 90)
        self.assertEqual(data.metrics["period_change_pct"], 10.0)
        self.assertEqual(data.metrics["max_drawdown_pct"], -10.0)
        self.assertEqual(len(rows), 3)

    def test_missing_news_never_creates_directional_trade_advice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(Path(tmp) / "state.db")
            quote = Quote("us_equities", "NVDA", "2026-06-09T14:00:00+00:00", 102, 100)
            store.save_quotes([quote])
            suggestion = build_suggestion(store, quote)
        self.assertEqual(suggestion["action"], "观察")
        self.assertEqual(suggestion["confidence"], "低")
        self.assertIn("缺少同期已验证资讯", suggestion["rationale"])

    def test_review_uses_only_suggestion_available_before_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(Path(tmp) / "state.db")
            store.save_suggestion(
                {
                    "market": "us_equities",
                    "symbol": "NVDA",
                    "created_at": "2026-06-09T13:00:00+00:00",
                    "action": "观察",
                    "confidence": "低",
                    "rationale": "before",
                    "evidence_ids": [],
                    "invalidation": "new evidence",
                }
            )
            store.save_suggestion(
                {
                    "market": "us_equities",
                    "symbol": "NVDA",
                    "created_at": "2026-06-09T15:00:00+00:00",
                    "action": "观察",
                    "confidence": "低",
                    "rationale": "after",
                    "evidence_ids": [],
                    "invalidation": "new evidence",
                }
            )
            store.save_operation(
                {
                    "market": "us_equities",
                    "symbol": "NVDA",
                    "operated_at": "2026-06-09T14:00:00+00:00",
                    "action": "buy",
                    "quantity": 1,
                    "price": 100,
                    "note": "",
                }
            )
            store.save_quotes(
                [Quote("us_equities", "NVDA", "2026-06-09T16:00:00+00:00", 105)]
            )
            review = build_daily_review(store, "us_equities", "2026-06-09")
        self.assertEqual(review["operations"][0]["available_suggestion"]["rationale"], "before")
        self.assertEqual(review["operations"][0]["subsequent_return_pct"], 5.0)

    def test_discussion_transcript_is_linked_to_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(Path(tmp) / "state.db")
            suggestion_id = store.save_suggestion(
                {
                    "market": "us_equities",
                    "symbol": "NVDA",
                    "created_at": "2026-06-09T14:00:00+00:00",
                    "action": "观察",
                    "confidence": "低",
                    "rationale": "证据不足。",
                    "evidence_ids": [],
                    "invalidation": "新证据出现。",
                }
            )
            store.save_discussion(
                suggestion_id,
                [DebateTurn("market_analyst", 0, "price view")],
            )
            rows = store.discussion_for_suggestion(suggestion_id)
        self.assertEqual((rows[0]["role"], rows[0]["content"]), ("market_analyst", "price view"))


if __name__ == "__main__":
    unittest.main()
