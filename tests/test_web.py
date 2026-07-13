import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from marketanalyzeragents.analysis_system import (
    ContentPack,
    dashboard_state,
    delete_holding,
    delete_topic,
    generate_market_report,
    _filter_social_posts_for_beijing_day,
    update_portfolio_configuration,
    update_model_configuration,
    update_source_configuration,
    upsert_holding,
    upsert_topic,
)
from marketanalyzeragents.social_adapters import SocialPost
from marketanalyzeragents.web import ReportRunState


VERIFIED_MRVL = {
    "ticker": "MRVL", "symbol": "MRVL", "company": "Marvell Technology, Inc.",
    "company_name_zh": "迈威尔科技", "company_name_en": "Marvell Technology, Inc.",
    "themes": ["Semiconductors"], "business_domains": ["Semiconductors"],
    "official_sources": [{"name": "SEC EDGAR", "url": "https://www.sec.gov/edgar/browse/?CIK=MRVL", "type": "disclosure"}],
    "verified": True,
}


class WebCoreTests(unittest.TestCase):
    def _write_project(self, root: Path) -> None:
        (root / "config").mkdir()
        (root / "config" / "settings.json").write_text(
            json.dumps(
                {
                    "backend": "dry-run",
                    "timezone": "Asia/Shanghai",
                    "sources_path": "config/sources.json",
                    "report_schedule": ["08:00", "14:00", "20:00"],
                    "state": {"analysis_dir": "state/analysis"},
                    "market_config_paths": {},
                    "market_overview": {"enabled": False},
                    "official_sources": [],
                }
            ),
            encoding="utf-8",
        )
        (root / "config" / "sources.json").write_text(
            json.dumps(
                {
                    "portfolios": {
                        "a_share": {"holdings": []},
                        "us_equities": {
                            "holdings": [
                                {
                                    "ticker": "NVDA",
                                    "symbol": "NVDA",
                                    "company": "NVIDIA",
                                    "themes": ["AI accelerators"],
                                }
                            ]
                        },
                    },
                    "focus_topics": [{"id": "ai", "name": "AI", "keywords": ["GPU"]}],
                    "official_sources": [],
                    "social_sources": {
                        "x": {
                            "enabled": True,
                            "accounts": ["analyst"],
                            "adapter": "disabled",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_dashboard_state_reads_new_core_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)

            with patch(
                "marketanalyzeragents.analysis_system.current_market_sentiment",
                return_value=({"value": "61", "label": "Greed", "status": "ok", "components": []}, []),
            ):
                state = dashboard_state(root)

        self.assertEqual(state["holdings"][0]["ticker"], "NVDA")
        self.assertEqual(state["focus_topics"][0]["source"], "holding")
        self.assertEqual(state["custom_focus_topics"][0]["keywords"], ["GPU"])
        self.assertIn("NVDA", state["social_keywords"])
        self.assertIn("GPU", state["social_keywords"])
        self.assertEqual(state["market_sentiment"]["value"], "61")
        self.assertNotIn("fear_greed", state)
        self.assertEqual(state["report_schedule"], ["08:00", "14:00", "20:00"])
        self.assertEqual(state["configuration"]["backend"], "dry-run")
        self.assertEqual(state["social_sources"]["x"]["accounts"], ["analyst"])
        self.assertIn("+08:00", state["generated_at"])

    def test_upsert_and_delete_holding_update_sources_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)

            with patch("marketanalyzeragents.analysis_system.lookup_stock_profile", return_value=VERIFIED_MRVL):
                upsert_holding(root, {"market": "us_equities", "ticker": "MRVL"})
            delete_holding(root, {"market": "us_equities", "ticker": "NVDA"})

            sources = json.loads((root / "config" / "sources.json").read_text(encoding="utf-8"))

        holdings = sources["portfolios"]["us_equities"]["holdings"]
        self.assertEqual([holding["ticker"] for holding in holdings], ["MRVL"])

    def test_holding_and_market_nav_are_preserved_by_market(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            with patch("marketanalyzeragents.analysis_system.lookup_stock_profile", return_value=VERIFIED_MRVL):
                upsert_holding(root, {"market": "us_equities", "ticker": "MRVL", "quantity": "12", "cost_basis": "68.5"})
            update_portfolio_configuration(root, {"market": "us_equities", "portfolio_nav": "250000", "currency": "USD"})
            with patch("marketanalyzeragents.analysis_system.current_market_sentiment", return_value=({"status": "ok"}, [])):
                state = dashboard_state(root)

        holding = next(item for item in state["market_views"]["us_equities"]["holdings"] if item["ticker"] == "MRVL")
        self.assertEqual(holding["quantity"], 12.0)
        self.assertEqual(holding["cost_basis"], 68.5)
        self.assertEqual(state["market_views"]["us_equities"]["portfolio_nav"], 250000.0)
        self.assertEqual(state["market_views"]["a_share"]["holdings"], [])

    def test_social_posts_are_filtered_to_current_beijing_day(self) -> None:
        run_at = datetime.fromisoformat("2026-07-13T10:00:00+08:00")
        posts = [
            SocialPost("x", "today", "2026-07-12T16:30:00+00:00", "u1", "today"),
            SocialPost("x", "yesterday", "2026-07-12T15:59:59+00:00", "u2", "old"),
        ]
        filtered, warnings = _filter_social_posts_for_beijing_day(posts, run_at)

        self.assertEqual([post.author for post in filtered], ["today"])
        self.assertEqual(warnings, [])

    def test_model_failure_degrades_each_section_and_keeps_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            with patch("marketanalyzeragents.analysis_system.collect_content", return_value=ContentPack([], [], [])), patch(
                "marketanalyzeragents.analysis_system.collect_market_overview", return_value=({"indices": [], "holdings": []}, [])
            ), patch("marketanalyzeragents.analysis_system.current_market_sentiment", return_value=({"status": "ok"}, [])), patch(
                "marketanalyzeragents.analysis_system._call_model", side_effect=RuntimeError("provider unavailable")
            ):
                result = generate_market_report(root, "us_equities", backend="zhipu")

        self.assertEqual(result["status"], "completed_with_warnings")
        self.assertIn("markdown", result)
        self.assertTrue(any(node["node"] == "model" for node in result["generation_nodes"]))
        self.assertIn("运行失败，已降级", result["markdown"])

    def test_topic_and_source_configuration_update_sources_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)

            upsert_topic(root, {"id": "energy", "name": "能源", "keywords": "power\nutility"})
            delete_topic(root, {"id": "ai"})
            update_source_configuration(
                root,
                {
                    "official_sources": [{"type": "rss", "enabled": True, "name": "Fed", "url": "https://example.com/rss"}],
                    "social_sources": {
                        "x": {
                            "enabled": True,
                            "accounts": ["macro_analyst"],
                            "keyword_max_results": 12,
                            "account_max_results_per_account": 8,
                        }
                    },
                },
            )

            sources = json.loads((root / "config" / "sources.json").read_text(encoding="utf-8"))

        self.assertEqual(sources["focus_topics"][0]["name_zh"], "能源")
        self.assertEqual(sources["focus_topics"][0]["name_en"], "power")
        self.assertEqual(sources["focus_topics"][0]["bilingual_status"], "complete")
        self.assertIn("utility", sources["focus_topics"][0]["keywords"])
        self.assertEqual(sources["official_sources"][0]["name"], "Fed")
        self.assertEqual(sources["social_sources"]["x"]["accounts"], ["macro_analyst"])
        self.assertEqual(sources["social_sources"]["x"]["keyword_max_results"], 12)
        self.assertEqual(sources["social_sources"]["x"]["account_max_results_per_account"], 8)
        self.assertNotIn("manual_posts", sources["social_sources"]["x"])
        self.assertIn("NVDA", sources["social_sources"]["x"]["keywords"])
        self.assertIn("power", sources["social_sources"]["x"]["keywords"])
        self.assertNotIn("fear_greed", sources)

    def test_model_configuration_updates_settings_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)

            config = update_model_configuration(
                root,
                {
                    "backend": "openai",
                    "model": "gpt-test",
                    "openai_model": "gpt-test",
                    "zhipu_model": "glm-test",
                    "openai_api_key": "sk-test",
                    "zhipu_api_key": "zhipu-test",
                    "advice_backend": "dry-run",
                    "debate_rounds": "2",
                    "report_schedule": "09:00,15:30",
                    "intraday_suggestion_interval_seconds": "600",
                },
            )
            settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))

        self.assertEqual(config["backend"], "openai")
        self.assertEqual(settings["openai"]["model"], "gpt-test")
        self.assertEqual(settings["zhipu"]["model"], "glm-test")
        self.assertEqual(settings["openai"]["api_key"], "sk-test")
        self.assertEqual(settings["zhipu"]["api_key"], "zhipu-test")
        self.assertTrue(config["openai_api_key_set"])
        self.assertTrue(config["zhipu_api_key_set"])
        self.assertEqual(settings["intraday_agents"]["advice_backend"], "openai")
        self.assertEqual(settings["report_schedule"], ["09:00", "15:30"])
        self.assertEqual(settings["intraday_suggestion_interval_seconds"], 600)

    def test_dry_run_report_creates_json_and_html_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            with patch("marketanalyzeragents.analysis_system._collect_official", return_value=([], [])), patch(
                "marketanalyzeragents.analysis_system.current_market_sentiment",
                return_value=({"value": "61", "label": "Greed", "status": "ok", "components": []}, []),
            ):
                report = generate_market_report(root, "us_equities", slot="08:00", backend="dry-run")
                html_exists = Path(report["html_path"]).exists()
                state = dashboard_state(root)

        self.assertIn("市场分析报告", report["markdown"])
        self.assertTrue(html_exists)
        self.assertEqual(state["latest_report"]["id"], report["id"])

    def test_report_includes_market_overview_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
            settings["market_overview"] = {
                "enabled": True,
                "indices": {"us_equities": [{"symbol": "^GSPC", "name": "标普 500"}]},
            }
            (root / "config" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

            def fake_fetch(_client, market, symbol, _settings):
                return SimpleNamespace(
                    quote=SimpleNamespace(
                        market=market,
                        symbol=symbol,
                        observed_at="2026-07-09T08:00:00+00:00",
                        price=5100.0 if symbol == "^GSPC" else 120.0,
                        previous_close=5000.0 if symbol == "^GSPC" else 100.0,
                    ),
                    metrics={"period_change_pct": 3.25},
                )

            with patch("marketanalyzeragents.analysis_system._collect_official", return_value=([], [])), patch(
                "marketanalyzeragents.analysis_system.current_market_sentiment",
                return_value=({"value": "61", "label": "Greed", "status": "ok", "components": []}, []),
            ), patch("marketanalyzeragents.analysis_system.fetch_market_data", side_effect=fake_fetch):
                report = generate_market_report(root, "us_equities", slot="08:00", backend="dry-run")

        index_symbols = [item["symbol"] for item in report["market_overview"]["indices"]]
        self.assertIn("^GSPC", index_symbols)
        self.assertEqual(report["market_overview"]["holdings"][0]["symbol"], "NVDA")
        self.assertIn("## 一、市场整体概况", report["markdown"])
        self.assertIn("标普 500", report["markdown"])
        self.assertIn("NVIDIA", report["markdown"])

    def test_report_keeps_configured_blogger_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            pack = ContentPack(
                official=[],
                social_posts=[
                    SocialPost(
                        platform="x",
                        author="search_author",
                        published_at="2026-07-12T09:00:00+00:00",
                        url="https://x.com/search_author/status/1",
                        text="NVDA GPU demand remains positive",
                        sentiment="positive",
                        collection_type="keyword",
                    ),
                    SocialPost(
                        platform="x",
                        author="analyst",
                        published_at="2026-07-12T10:00:00+00:00",
                        url="https://x.com/analyst/status/2",
                        text="GPU ROI is becoming the key AI infrastructure question",
                        sentiment="neutral",
                        collection_type="account",
                    ),
                ],
                source_warnings=[],
            )

            with patch("marketanalyzeragents.analysis_system.collect_content", return_value=pack), patch(
                "marketanalyzeragents.analysis_system.current_market_sentiment",
                return_value=({"value": "61", "label": "Greed", "status": "ok", "components": []}, []),
            ):
                report = generate_market_report(root, "us_equities", slot="08:00", backend="dry-run")

        self.assertIn("### 配置博主观点", report["markdown"])
        self.assertIn("analyst", report["markdown"])
        self.assertIn("GPU ROI", report["markdown"])
        self.assertEqual(report["social_count"], 2)

    def test_report_uses_one_social_model_call_for_all_configured_bloggers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            sources_path = root / "config" / "sources.json"
            sources = json.loads(sources_path.read_text(encoding="utf-8"))
            sources["social_sources"]["x"]["accounts"] = ["analyst", "second_analyst"]
            sources_path.write_text(json.dumps(sources), encoding="utf-8")
            pack = ContentPack(
                official=[],
                social_posts=[
                    SocialPost(
                        platform="x",
                        author="analyst",
                        published_at="2026-07-12T10:00:00+00:00",
                        url="https://x.com/analyst/status/2",
                        text="GPU ROI is becoming the key AI infrastructure question",
                        sentiment="neutral",
                        collection_type="account",
                    ),
                    SocialPost(
                        platform="x",
                        author="second_analyst",
                        published_at="2026-07-12T10:05:00+00:00",
                        url="https://x.com/second_analyst/status/3",
                        text="Data center power remains a constraint",
                        sentiment="neutral",
                        collection_type="account",
                    ),
                ],
                source_warnings=[],
            )

            with patch("marketanalyzeragents.analysis_system.collect_content", return_value=pack), patch(
                "marketanalyzeragents.analysis_system.current_market_sentiment",
                return_value=({"value": "61", "label": "Greed", "status": "ok", "components": []}, []),
            ), patch("marketanalyzeragents.analysis_system._call_model", return_value="") as call_model:
                generate_market_report(root, "us_equities", slot="08:00", backend="zhipu")

        self.assertEqual(call_model.call_count, 4)

    def test_report_run_state_completes_background_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            runner = ReportRunState()

            with patch(
                "marketanalyzeragents.web.generate_market_report",
                return_value={
                    "id": "20260710-080000-0800",
                    "title": "2026-07-10 08:00 市场分析报告",
                    "generated_at": "2026-07-10T08:00:00+08:00",
                    "official_count": 2,
                    "social_count": 1,
                },
            ):
                started = runner.start(root, "us_equities", backend="dry-run")
                for _ in range(50):
                    status = runner.snapshot()
                    if status["state"] == "completed":
                        break
                    time.sleep(0.01)

        self.assertEqual(started["state"], "running")
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["result"]["id"], "20260710-080000-0800")


if __name__ == "__main__":
    unittest.main()
