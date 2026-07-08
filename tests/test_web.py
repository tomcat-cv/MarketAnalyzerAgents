import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from marketanalyzeragents.analysis_system import (
    dashboard_state,
    delete_holding,
    delete_topic,
    generate_market_report,
    update_model_configuration,
    update_source_configuration,
    upsert_holding,
    upsert_topic,
)


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
                            "keywords": ["NVDA"],
                            "accounts": ["analyst"],
                            "manual_posts": [
                                {
                                    "author": "analyst",
                                    "text": "NVDA AI capex looks positive",
                                    "published_at": "2026-07-08T08:00:00+08:00",
                                }
                            ],
                        }
                    },
                    "fear_greed": {"value": "61", "label": "Greed"},
                }
            ),
            encoding="utf-8",
        )

    def test_dashboard_state_reads_new_core_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)

            state = dashboard_state(root)

        self.assertEqual(state["holdings"][0]["ticker"], "NVDA")
        self.assertEqual(state["focus_topics"][0]["keywords"], ["GPU"])
        self.assertEqual(state["report_schedule"], ["08:00", "14:00", "20:00"])
        self.assertEqual(state["configuration"]["backend"], "dry-run")
        self.assertEqual(state["social_sources"]["x"]["accounts"], ["analyst"])
        self.assertIn("+08:00", state["generated_at"])

    def test_upsert_and_delete_holding_update_sources_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)

            upsert_holding(
                root,
                {
                    "market": "us_equities",
                    "ticker": "MRVL",
                    "symbol": "MRVL",
                    "company": "Marvell",
                    "themes": ["custom silicon"],
                },
            )
            delete_holding(root, {"market": "us_equities", "ticker": "NVDA"})

            sources = json.loads((root / "config" / "sources.json").read_text(encoding="utf-8"))

        holdings = sources["portfolios"]["us_equities"]["holdings"]
        self.assertEqual([holding["ticker"] for holding in holdings], ["MRVL"])

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
                    "social_sources": {"x": {"enabled": True, "keywords": ["NVDA"], "accounts": []}},
                    "fear_greed": {"value": "35", "label": "Fear", "source_url": "https://example.com/fear"},
                },
            )

            sources = json.loads((root / "config" / "sources.json").read_text(encoding="utf-8"))

        self.assertEqual(sources["focus_topics"], [{"id": "energy", "name": "能源", "keywords": ["power", "utility"]}])
        self.assertEqual(sources["official_sources"][0]["name"], "Fed")
        self.assertEqual(sources["fear_greed"]["label"], "Fear")
        self.assertEqual(sources["fear_greed"]["source_url"], "https://example.com/fear")

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
        self.assertEqual(settings["intraday_agents"]["advice_backend"], "dry-run")
        self.assertEqual(settings["report_schedule"], ["09:00", "15:30"])
        self.assertEqual(settings["intraday_suggestion_interval_seconds"], 600)

    def test_dry_run_report_creates_json_and_html_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            with patch("marketanalyzeragents.analysis_system._collect_official", return_value=([], [])):
                report = generate_market_report(root, slot="08:00", backend="dry-run")
                html_exists = Path(report["html_path"]).exists()
                state = dashboard_state(root)

        self.assertIn("市场分析报告", report["markdown"])
        self.assertTrue(html_exists)
        self.assertEqual(state["latest_report"]["id"], report["id"])


if __name__ == "__main__":
    unittest.main()
