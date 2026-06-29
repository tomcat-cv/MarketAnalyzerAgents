import json
import tempfile
import unittest
from pathlib import Path

from marketanalyzeragents.web import (
    INDEX_HTML,
    delete_focus_topic,
    delete_holding,
    load_dashboard_state,
    update_model_configuration,
    upsert_focus_topic,
    upsert_holding,
)


class WebDashboardTests(unittest.TestCase):
    def _write_project(self, root: Path) -> None:
        (root / "config").mkdir()
        (root / "briefs" / "us_equities").mkdir(parents=True)
        (root / "config" / "settings.json").write_text(
            json.dumps(
                {
                    "sources_path": "config/sources.json",
                    "state": {
                        "database_path": "state/portfolio.db",
                        "conversation_outbox": "state/outbox.jsonl",
                    },
                    "market_config_paths": {},
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
                    }
                }
            ),
            encoding="utf-8",
        )
        (root / "briefs" / "us_equities" / "2026-06-23-brief.html").write_text(
            "<!doctype html><title>Brief</title>",
            encoding="utf-8",
        )

    def test_dashboard_state_reads_holdings_and_briefs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)

            state = load_dashboard_state(root)

        self.assertEqual(state["holdings"][0]["ticker"], "NVDA")
        self.assertEqual(state["briefs"][0]["market"], "us_equities")
        self.assertIn("us_equities", state["markets"])
        self.assertEqual(state["configuration"]["backend"], "zhipu")

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
                    "themes": ["custom silicon", "optical DSP"],
                },
            )
            delete_holding(root, {"market": "us_equities", "ticker": "NVDA"})

            sources = json.loads((root / "config" / "sources.json").read_text(encoding="utf-8"))

        holdings = sources["portfolios"]["us_equities"]["holdings"]
        self.assertEqual([holding["ticker"] for holding in holdings], ["MRVL"])
        self.assertEqual(holdings[0]["themes"], ["custom silicon", "optical DSP"])

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
                    "advice_backend": "zhipu",
                    "debate_rounds": "2",
                },
            )

            settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))

        self.assertEqual(config["backend"], "openai")
        self.assertEqual(settings["backend"], "openai")
        self.assertEqual(settings["model"], "gpt-test")
        self.assertEqual(settings["openai"]["model"], "gpt-test")
        self.assertEqual(settings["zhipu"]["model"], "glm-test")
        self.assertEqual(settings["intraday_agents"]["advice_backend"], "zhipu")
        self.assertEqual(settings["intraday_agents"]["debate_rounds"], 2)

    def test_upsert_and_delete_focus_topic_update_sources_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)

            upsert_focus_topic(
                root,
                {
                    "id": "semiconductors",
                    "name": "半导体",
                    "segments": [{"name": "美股半导体", "topics": ["主题:半导体:美股"]}],
                    "instruments": [
                        {
                            "symbol": "^SOX",
                            "name": "PHLX Semiconductor Index",
                            "topics": ["主题:半导体:美股"],
                        }
                    ],
                },
            )
            state = load_dashboard_state(root)
            delete_focus_topic(root, {"id": "semiconductors"})

            sources = json.loads((root / "config" / "sources.json").read_text(encoding="utf-8"))

        self.assertEqual(state["focus_topics"][0]["id"], "semiconductors")
        self.assertEqual(sources["focus_topics"], [])

    def test_home_rendering_is_read_only_and_config_keeps_actions(self) -> None:
        home_renderer = INDEX_HTML.split("function renderHoldings", 1)[1].split("function renderHoldingConfig", 1)[0]
        config_renderer = INDEX_HTML.split("function renderHoldingConfig", 1)[1].split("function renderAlerts", 1)[0]

        self.assertNotIn("data-edit-holding", home_renderer)
        self.assertNotIn("data-delete", home_renderer)
        self.assertIn("data-edit-holding", config_renderer)
        self.assertIn("data-delete", config_renderer)


if __name__ == "__main__":
    unittest.main()
