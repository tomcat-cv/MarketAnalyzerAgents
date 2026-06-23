import unittest
import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from marketanalyzeragents.cli import _configured_intraday_markets, build_parser, command_intraday
from marketanalyzeragents.intraday import MarketData
from marketanalyzeragents.portfolio_store import PriceBar, Quote


class ServiceCommandTests(unittest.TestCase):
    def test_service_defaults_to_markets_with_configured_holdings(self) -> None:
        inputs = {
            "sources": {
                "portfolios": {
                    "a_share": {"holdings": []},
                    "us_equities": {"holdings": [{"ticker": "NVDA", "company": "NVIDIA"}]},
                }
            }
        }

        markets = _configured_intraday_markets(inputs, None)

        self.assertEqual(markets, ["us_equities"])

    def test_service_respects_explicit_market_selection(self) -> None:
        markets = _configured_intraday_markets({"sources": {}}, ["us_equities", "us_equities"])

        self.assertEqual(markets, ["us_equities"])

    def test_service_command_exposes_unified_runtime_options(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "service",
                "--markets",
                "us_equities",
                "--with-news",
                "--advice-backend",
                "conservative",
                "--interval",
                "60",
            ]
        )

        self.assertEqual(args.command, "service")
        self.assertEqual(args.markets, ["us_equities"])
        self.assertTrue(args.with_news)
        self.assertEqual(args.advice_backend, "conservative")
        self.assertEqual(args.interval, 60)

    def test_intraday_continues_when_one_holding_data_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "state").mkdir()
            (root / "config" / "settings.json").write_text(
                json.dumps(
                    {
                        "state": {
                            "database_path": "state/portfolio.db",
                            "conversation_outbox": "state/outbox.jsonl",
                        },
                        "feishu": {"webhook_url": ""},
                    }
                ),
                encoding="utf-8",
            )
            (root / "config" / "sources.json").write_text(
                json.dumps(
                    {
                        "portfolios": {
                            "us_equities": {
                                "holdings": [
                                    {"ticker": "MU", "symbol": "MU", "company": "Micron"},
                                    {"ticker": "NVDA", "symbol": "NVDA", "company": "NVIDIA"},
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                market="us_equities",
                watch=False,
                interval=None,
                force=True,
                with_news=False,
                advice_backend="conservative",
                debate_rounds=None,
            )

            def fake_fetch(client, market, symbol, *, history_range, history_interval):
                if symbol == "MU":
                    raise ValueError("No usable market data returned for MU")
                return MarketData(
                    quote=Quote("us_equities", "NVDA", "2026-06-22T21:31:00+08:00", 102, 100),
                    history=(
                        PriceBar(
                            "us_equities",
                            "NVDA",
                            "1d",
                            "2026-06-22T21:31:00+08:00",
                            100,
                            103,
                            99,
                            102,
                            1000,
                            "test",
                        ),
                    ),
                    metrics={},
                )

            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": ""}), patch(
                    "marketanalyzeragents.cli.fetch_yahoo_market_data",
                    side_effect=fake_fetch,
                ):
                    exit_code = command_intraday(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(exit_code, 0)
            con = sqlite3.connect(root / "state" / "portfolio.db")
            self.assertEqual(con.execute("select count(*) from suggestions").fetchone()[0], 1)
            self.assertEqual(
                con.execute("select symbol from suggestions").fetchone()[0],
                "NVDA",
            )
            rows = [
                json.loads(line)
                for line in (root / "state" / "outbox.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(rows[0]["symbol"], "NVDA")


if __name__ == "__main__":
    unittest.main()
