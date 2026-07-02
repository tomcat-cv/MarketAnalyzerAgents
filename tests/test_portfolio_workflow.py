from pathlib import Path
from datetime import datetime, timezone
import sqlite3
import tempfile
import unittest

from marketanalyzeragents.intraday import (
    build_suggestion,
    fetch_yahoo_market_data,
    should_emit_suggestion,
    should_run_agent_debate,
    yahoo_symbol,
)
from marketanalyzeragents.agent_debate import DebateTurn
from marketanalyzeragents.portfolio_store import PortfolioStore, Quote
from marketanalyzeragents.review import build_daily_review


class PortfolioWorkflowTests(unittest.TestCase):
    def test_store_enables_long_running_sqlite_pragmas_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.db"
            store = PortfolioStore(db_path)
            self.assertEqual(store.connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(store.connection.execute("PRAGMA busy_timeout").fetchone()[0], 30000)
            self.assertEqual(store.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(store.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            store.close()

            with sqlite3.connect(db_path) as con:
                indexes = {
                    row[0]
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    ).fetchall()
                }

        self.assertIn("idx_quotes_recent", indexes)
        self.assertIn("idx_price_bars_recent", indexes)
        self.assertIn("idx_suggestions_lookup", indexes)
        self.assertIn("idx_operations_review", indexes)
        self.assertIn("idx_agent_discussions_suggestion", indexes)
        self.assertIn("idx_evidence_items_recent", indexes)

    def test_store_persists_and_filters_recent_evidence_by_ticker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(Path(tmp) / "state.db")
            store.save_evidence_pack(
                {
                    "retrieved_at": "2026-06-23T17:58:00+00:00",
                    "lookback_hours": 24,
                    "window_start": "",
                    "window_end": "",
                    "window_mode": "rolling_hours",
                    "timezone": "Asia/Shanghai",
                    "items": [
                        {
                            "id": "EVID-001",
                            "title": "NVIDIA update",
                            "published_at": "2026-06-23T17:55:00+00:00",
                            "source_name": "Official",
                            "source_type": "primary",
                            "url": "https://example.test/nvda",
                            "display_url": "",
                            "content": "verified update",
                            "matched_topics": [],
                            "matched_tickers": ["NVDA"],
                            "evidence_level": "summary",
                        },
                        {
                            "id": "EVID-002",
                            "title": "Title only",
                            "published_at": "2026-06-23T17:56:00+00:00",
                            "source_name": "Official",
                            "source_type": "primary",
                            "url": "https://example.test/title",
                            "display_url": "",
                            "content": "title only",
                            "matched_topics": [],
                            "matched_tickers": ["NVDA"],
                            "evidence_level": "title_only",
                        },
                    ],
                    "errors": [],
                    "coverage": [],
                }
            )

            grouped = store.recent_summary_evidence_for_tickers(
                ["NVDA"],
                since=datetime(2026, 6, 23, 17, 0, tzinfo=timezone.utc),
            )
            store.close()

        self.assertEqual([item["id"] for item in grouped["NVDA"]], ["EVID-001"])

    def test_market_symbols_are_normalized_for_both_markets(self) -> None:
        self.assertEqual(yahoo_symbol("a_share", "600519"), "600519.SS")
        self.assertEqual(yahoo_symbol("a_share", "000001"), "000001.SZ")
        self.assertEqual(yahoo_symbol("a_share", "830799"), "830799.BJ")
        self.assertEqual(yahoo_symbol("us_equities", "nvda"), "NVDA")

    def test_store_prunes_only_old_market_data_not_audit_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(Path(tmp) / "state.db")
            store.save_quotes(
                [
                    Quote("us_equities", "NVDA", "2026-01-01T00:00:00+00:00", 100),
                    Quote("us_equities", "NVDA", "2026-06-01T00:00:00+00:00", 120),
                ]
            )
            store.save_suggestion(
                {
                    "market": "us_equities",
                    "symbol": "NVDA",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "action": "观察",
                    "confidence": "低",
                    "rationale": "audit record",
                    "evidence_ids": [],
                    "invalidation": "new evidence",
                }
            )

            pruned = store.prune_market_data(
                30,
                now=datetime(2026, 6, 15, tzinfo=timezone.utc),
            )
            quote_count = store.connection.execute("SELECT count(*) FROM quotes").fetchone()[0]
            suggestion_count = store.connection.execute("SELECT count(*) FROM suggestions").fetchone()[0]
            store.close()

        self.assertEqual(pruned["quotes"], 1)
        self.assertEqual(quote_count, 1)
        self.assertEqual(suggestion_count, 1)

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

    def test_routine_low_signal_observations_are_suppressed_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(Path(tmp) / "state.db")
            quote = Quote("us_equities", "MRVL", "2026-06-23T17:57:04+00:00", 100.04, 100)
            store.save_quotes([quote])
            suggestion = build_suggestion(store, quote)

        self.assertEqual(suggestion["signal"], "routine_poll")
        self.assertFalse(should_run_agent_debate(suggestion))
        self.assertFalse(should_emit_suggestion(suggestion))
        self.assertTrue(should_emit_suggestion(suggestion, emit_low_signal=True))

    def test_material_price_moves_without_news_are_not_emitted_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(Path(tmp) / "state.db")
            quote = Quote("us_equities", "MRVL", "2026-06-23T17:57:04+00:00", 103, 100)
            store.save_quotes([quote])
            suggestion = build_suggestion(store, quote)

        self.assertEqual(suggestion["signal"], "material_price_move")
        self.assertFalse(should_run_agent_debate(suggestion))
        self.assertFalse(should_emit_suggestion(suggestion))
        self.assertTrue(should_emit_suggestion(suggestion, emit_low_signal=True))

    def test_conservative_observations_with_news_are_not_suggestions_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(Path(tmp) / "state.db")
            quote = Quote("us_equities", "MRVL", "2026-06-23T17:57:04+00:00", 103, 100)
            store.save_quotes([quote])
            suggestion = build_suggestion(store, quote, ["E1"])

        self.assertEqual(suggestion["action"], "观察")
        self.assertEqual(suggestion["confidence"], "中")
        self.assertEqual(suggestion["decision_source"], "deterministic_observation")
        self.assertIn("未形成操作建议", suggestion["rationale"])
        self.assertTrue(should_run_agent_debate(suggestion))
        self.assertFalse(should_emit_suggestion(suggestion))

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
