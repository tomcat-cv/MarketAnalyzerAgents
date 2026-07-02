import unittest
import argparse
import contextlib
import io
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from marketanalyzeragents.cli import (
    _brief_delivery_path,
    _configured_brief_markets,
    _configured_intraday_markets,
    _market_filtered_inputs,
    _write_brief_outputs,
    build_parser,
    command_intraday,
)
from marketanalyzeragents.evidence import EvidenceItem, EvidencePack
from marketanalyzeragents.intraday import FeishuWebhookConversationPort, MarketData, format_conversation_message
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

    def test_brief_markets_default_to_independent_market_configs(self) -> None:
        markets = _configured_brief_markets(
            {
                "market_config_paths": {
                    "a_share": "config/markets/a_share.json",
                    "us_equities": "config/markets/us_equities.json",
                }
            }
        )

        self.assertEqual(markets, ["a_share", "us_equities"])

    def test_market_filtered_inputs_keep_shared_topics_but_only_requested_holdings(self) -> None:
        inputs = {
            "sources": {
                "focus_topics": [
                    {
                        "id": "semiconductors",
                        "segments": [
                            {"name": "A股半导体", "topics": ["主题:半导体:A股"]},
                            {"name": "美股半导体", "topics": ["主题:半导体:美股"]},
                        ],
                        "instruments": [
                            {"symbol": "512480.SS", "name": "A股半导体ETF代理"},
                            {"symbol": "^SOX", "name": "PHLX Semiconductor Index"},
                        ],
                    },
                    {"id": "gold", "name": "黄金", "instruments": [{"symbol": "GC=F"}]},
                ],
                "portfolios": {
                    "a_share": {"holdings": [{"ticker": "688001"}]},
                    "us_equities": {"holdings": [{"ticker": "NVDA"}]},
                },
                "collectors": {
                    "yahoo_market_snapshots": {
                        "instruments": [
                            {"symbol": "000001.SS", "name": "上证指数", "topics": ["A股"]},
                            {"symbol": "^GSPC", "name": "S&P 500", "topics": ["美股整体市场"]},
                            {"symbol": "GC=F", "name": "COMEX Gold Futures", "topics": ["主题:黄金"]},
                        ]
                    }
                },
            }
        }

        filtered = _market_filtered_inputs(inputs, "us_equities")

        self.assertEqual(filtered["sources"]["portfolios"]["a_share"]["holdings"], [])
        self.assertEqual(filtered["sources"]["portfolios"]["us_equities"]["holdings"], [{"ticker": "NVDA"}])
        self.assertEqual(
            filtered["sources"]["focus_topics"][0]["segments"],
            [{"name": "美股半导体", "topics": ["主题:半导体:美股"]}],
        )
        self.assertEqual(
            [item["symbol"] for item in filtered["sources"]["focus_topics"][0]["instruments"]],
            ["^SOX"],
        )
        self.assertEqual(
            [item["symbol"] for item in filtered["sources"]["collectors"]["yahoo_market_snapshots"]["instruments"]],
            ["^GSPC", "GC=F"],
        )

    def test_feishu_formatter_renders_brief_link(self) -> None:
        text = format_conversation_message(
            {
                "type": "pre_market_brief",
                "market": "us_equities",
                "date": "2026-06-23",
                "output": "briefs/us_equities/2026-06-23-brief.html",
                "url": "http://example.test/us_equities/2026-06-23-brief.html",
                "generated_at": "2026-06-23T20:00:00+08:00",
            }
        )

        self.assertIn("盘前简报已生成 [us_equities]", text)
        self.assertIn("http://example.test/us_equities/2026-06-23-brief.html", text)

    def test_brief_delivery_prefers_html_copy_for_markdown_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "briefs" / "us_equities" / "2026-06-23-brief.md"

            delivery_path = _write_brief_outputs(output_path, "# 美股盘前研究简报 - 2026-06-23\n\n正文")

            self.assertEqual(delivery_path, output_path.with_suffix(".html"))
            self.assertEqual(_brief_delivery_path(output_path), output_path.with_suffix(".html"))
            self.assertIn('<meta charset="utf-8">', delivery_path.read_text(encoding="utf-8"))

    def test_feishu_webhook_posts_text_payload(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"StatusCode":0}'

        def fake_urlopen(request, timeout):
            captured["body"] = request.data.decode("utf-8")
            captured["timeout"] = timeout
            return FakeResponse()

        port = FeishuWebhookConversationPort("https://example.test/webhook", timeout=3)
        with patch("marketanalyzeragents.intraday.urllib.request.urlopen", side_effect=fake_urlopen):
            port.deliver(
                {
                    "market": "us_equities",
                    "symbol": "NVDA",
                    "created_at": "2026-06-22T22:00:00+08:00",
                    "action": "观察",
                    "confidence": "低",
                    "rationale": "测试",
                    "evidence_ids": [],
                    "invalidation": "测试结束",
                }
            )

        payload = json.loads(captured["body"])
        self.assertEqual(payload["msg_type"], "text")
        self.assertIn("盘中定时分析 [us_equities NVDA]", payload["content"]["text"])
        self.assertEqual(captured["timeout"], 3)

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
                "--emit-low-signal",
            ]
        )

        self.assertEqual(args.command, "service")
        self.assertEqual(args.markets, ["us_equities"])
        self.assertTrue(args.with_news)
        self.assertFalse(args.no_news_watch)
        self.assertIsNone(args.news_interval)
        self.assertEqual(args.advice_backend, "conservative")
        self.assertEqual(args.interval, 60)
        self.assertTrue(args.emit_low_signal)

    def test_intraday_suppresses_routine_low_signal_notifications(self) -> None:
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
                        }
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
                                    {"ticker": "MRVL", "symbol": "MRVL", "company": "Marvell"}
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
                advice_backend="openai",
                debate_rounds=None,
                emit_low_signal=False,
            )

            def fake_fetch(client, market, symbol, *, history_range, history_interval):
                return MarketData(
                    quote=Quote("us_equities", "MRVL", "2026-06-23T17:57:04+00:00", 100.04, 100),
                    history=(
                        PriceBar(
                            "us_equities",
                            "MRVL",
                            "1d",
                            "2026-06-23T17:57:04+00:00",
                            100,
                            101,
                            99,
                            100.04,
                            1000,
                            "test",
                        ),
                    ),
                    metrics={},
                )

            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch(
                    "marketanalyzeragents.cli.fetch_yahoo_market_data",
                    side_effect=fake_fetch,
                ), patch(
                    "marketanalyzeragents.cli.run_agent_debate",
                    return_value=argparse.Namespace(
                        suggestion={
                            "market": "us_equities",
                            "symbol": "NVDA",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "action": "观察",
                            "confidence": "中",
                            "rationale": "stored evidence reached the advisor agents",
                            "evidence_ids": ["EVID-001"],
                            "invalidation": "new evidence",
                        },
                        turns=[],
                    ),
                ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    exit_code = command_intraday(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(exit_code, 0)
            con = sqlite3.connect(root / "state" / "portfolio.db")
            self.assertEqual(con.execute("select count(*) from quotes").fetchone()[0], 1)
            self.assertEqual(con.execute("select count(*) from suggestions").fetchone()[0], 0)
            self.assertFalse((root / "state" / "outbox.jsonl").exists())

    def test_intraday_uses_stored_evidence_without_immediate_news_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "state").mkdir()
            (root / "config" / "settings.json").write_text(
                json.dumps(
                    {
                        "lookback_hours": 24,
                        "state": {
                            "database_path": "state/portfolio.db",
                            "conversation_outbox": "state/outbox.jsonl",
                        },
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
                                    {"ticker": "NVDA", "symbol": "NVDA", "company": "NVIDIA"}
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            con = sqlite3.connect(root / "state" / "portfolio.db")
            con.close()
            from marketanalyzeragents.portfolio_store import PortfolioStore

            store = PortfolioStore(root / "state" / "portfolio.db")
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
                            "title": "NVIDIA verified update",
                            "published_at": datetime.now(timezone.utc).isoformat(),
                            "source_name": "Official",
                            "source_type": "primary",
                            "url": "https://example.test/nvda",
                            "display_url": "",
                            "content": "verified update",
                            "matched_topics": [],
                            "matched_tickers": ["NVDA"],
                            "evidence_level": "summary",
                        }
                    ],
                    "errors": [],
                    "coverage": [],
                }
            )
            store.close()
            args = argparse.Namespace(
                market="us_equities",
                watch=False,
                interval=None,
                force=True,
                with_news=False,
                advice_backend="openai",
                debate_rounds=None,
                emit_low_signal=False,
            )

            def fake_fetch(client, market, symbol, *, history_range, history_interval):
                return MarketData(
                    quote=Quote("us_equities", "NVDA", datetime.now(timezone.utc).isoformat(), 103, 100),
                    history=(),
                    metrics={},
                )

            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch(
                    "marketanalyzeragents.cli.fetch_yahoo_market_data",
                    side_effect=fake_fetch,
                ), patch(
                    "marketanalyzeragents.cli.run_agent_debate",
                    return_value=argparse.Namespace(
                        suggestion={
                            "market": "us_equities",
                            "symbol": "NVDA",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "action": "观察",
                            "confidence": "中",
                            "rationale": "stored evidence reached the advisor agents",
                            "evidence_ids": ["EVID-001"],
                            "invalidation": "new evidence",
                        },
                        turns=[],
                    ),
                ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    exit_code = command_intraday(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(exit_code, 0)
            con = sqlite3.connect(root / "state" / "portfolio.db")
            row = con.execute("select evidence_json from suggestions").fetchone()
            self.assertEqual(json.loads(row[0]), ["EVID-001"])

    def test_intraday_uses_configured_advice_backend_when_cli_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "state").mkdir()
            (root / "config" / "settings.json").write_text(
                json.dumps(
                    {
                        "intraday_agents": {"advice_backend": "openai"},
                        "state": {
                            "database_path": "state/portfolio.db",
                            "conversation_outbox": "state/outbox.jsonl",
                        },
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
                                    {"ticker": "MRVL", "symbol": "MRVL", "company": "Marvell"}
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
                with_news=True,
                advice_backend=None,
                debate_rounds=None,
                emit_low_signal=False,
            )

            def fake_fetch(client, market, symbol, *, history_range, history_interval):
                return MarketData(
                    quote=Quote("us_equities", "MRVL", "2026-06-23T17:57:04+00:00", 103, 100),
                    history=(
                        PriceBar(
                            "us_equities",
                            "MRVL",
                            "1d",
                            "2026-06-23T17:57:04+00:00",
                            100,
                            104,
                            99,
                            103,
                            1000,
                            "test",
                        ),
                    ),
                    metrics={},
                )

            def fake_debate(**kwargs):
                return argparse.Namespace(
                    suggestion={
                        "market": "us_equities",
                        "symbol": "MRVL",
                        "created_at": "2026-06-23T17:57:04+00:00",
                        "action": "观察",
                        "confidence": "中",
                        "rationale": "configured backend was used",
                        "evidence_ids": ["E1"],
                        "invalidation": "test",
                    },
                    turns=[],
                )

            evidence = EvidencePack(
                "2026-06-23T17:57:04+00:00",
                24,
                items=[
                    EvidenceItem(
                        "E1",
                        "Marvell verified update",
                        "2026-06-23T17:40:00+00:00",
                        "Official",
                        "company",
                        "https://example.test/mrvl",
                        "verified update",
                        matched_tickers=["MRVL"],
                    )
                ],
            )

            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch(
                    "marketanalyzeragents.cli.fetch_yahoo_market_data",
                    side_effect=fake_fetch,
                ), patch(
                    "marketanalyzeragents.cli.collect_evidence",
                    return_value=evidence,
                ), patch(
                    "marketanalyzeragents.cli.run_agent_debate",
                    side_effect=fake_debate,
                ) as debate, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    exit_code = command_intraday(args)
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(exit_code, 0)
        self.assertTrue(debate.called)

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
                        }
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
                with_news=True,
                advice_backend="conservative",
                debate_rounds=None,
                emit_low_signal=False,
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

            evidence = EvidencePack(
                "2026-06-22T21:31:00+08:00",
                24,
                items=[
                    EvidenceItem(
                        "E1",
                        "NVIDIA verified update",
                        "2026-06-22T21:00:00+08:00",
                        "Official",
                        "company",
                        "https://example.test/nvda",
                        "verified update",
                        matched_tickers=["NVDA"],
                    )
                ],
            )

            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch(
                    "marketanalyzeragents.cli.fetch_yahoo_market_data",
                    side_effect=fake_fetch,
                ), patch(
                    "marketanalyzeragents.cli.collect_evidence",
                    return_value=evidence,
                ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    exit_code = command_intraday(args)
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(exit_code, 0)
            con = sqlite3.connect(root / "state" / "portfolio.db")
            self.assertEqual(con.execute("select count(*) from quotes").fetchone()[0], 1)
            self.assertEqual(con.execute("select count(*) from suggestions").fetchone()[0], 0)
            self.assertEqual(
                con.execute("select symbol from quotes").fetchone()[0],
                "NVDA",
            )
            self.assertFalse((root / "state" / "outbox.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
