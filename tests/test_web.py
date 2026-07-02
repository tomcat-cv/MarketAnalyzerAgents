import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from marketanalyzeragents.intraday import MarketData
from marketanalyzeragents.portfolio_store import PriceBar, Quote
from marketanalyzeragents.web import (
    INDEX_HTML,
    STATIC_DIR,
    confirm_feishu_portfolio_import,
    create_feishu_portfolio_import,
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
        self.assertEqual(state["display_timezone"], "Asia/Shanghai")
        self.assertEqual(state["briefs"][0]["timezone"], "Asia/Shanghai")
        self.assertIn("+08:00", state["generated_at"])
        self.assertIn("+08:00", state["briefs"][0]["modified_at"])
        self.assertIn("us_equities", state["markets"])
        self.assertEqual(state["configuration"]["backend"], "zhipu")

    def test_dashboard_state_prefers_confirmed_portfolio_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
            from marketanalyzeragents.portfolio_store import PortfolioStore

            with PortfolioStore(root / "state" / "portfolio.db") as store:
                store.save_portfolio_snapshot(
                    "us_equities",
                    [{"ticker": "MRVL", "company": "Marvell"}],
                    created_at="2026-06-24T09:00:00+08:00",
                    source_type="test",
                    status="confirmed",
                )

            state = load_dashboard_state(root)

        self.assertEqual([holding["ticker"] for holding in state["holdings"]], ["MRVL"])

    def test_dashboard_refresh_fetches_current_quotes_before_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            settings_path = root / "config" / "settings.json"
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            settings["market_data"] = {
                "provider": "yahoo",
                "history_range": "1mo",
                "history_interval": "1d",
            }
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
            quote = Quote("us_equities", "NVDA", "2026-06-29T13:30:00+00:00", 125.5, 120.0)
            history = (
                PriceBar("us_equities", "NVDA", "1d", "2026-06-28T00:00:00+00:00", 119, 121, 118, 120, 100),
                PriceBar("us_equities", "NVDA", "1d", "2026-06-29T00:00:00+00:00", 120, 126, 119, 125.5, 120),
            )

            with patch(
                "marketanalyzeragents.web.fetch_market_data",
                return_value=MarketData(quote=quote, history=history, metrics={}),
            ) as fetch:
                state = load_dashboard_state(root, refresh_quotes=True)

        fetch.assert_called_once()
        self.assertTrue(state["quote_refresh"]["attempted"])
        self.assertEqual(state["quote_refresh"]["failures"], [])
        self.assertEqual(state["holdings"][0]["quote"]["price"], 125.5)
        self.assertEqual(state["holdings"][0]["quote"]["observed_at"], "2026-06-29T13:30:00+00:00")

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

    def test_holding_update_rejects_invalid_theme_payload_without_rewriting_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            before = (root / "config" / "sources.json").read_text(encoding="utf-8")

            with self.assertRaises(ValueError):
                upsert_holding(
                    root,
                    {
                        "market": "us_equities",
                        "ticker": "MRVL",
                        "themes": {"invalid": "shape"},
                    },
                )

            after = (root / "config" / "sources.json").read_text(encoding="utf-8")

        self.assertEqual(after, before)

    def test_holding_update_accepts_newline_delimited_theme_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)

            holding = upsert_holding(
                root,
                {
                    "market": "us_equities",
                    "ticker": "MRVL",
                    "themes": "custom silicon\noptical DSP",
                },
            )

        self.assertEqual(holding["themes"], ["custom silicon", "optical DSP"])

    def test_feishu_import_requires_confirmation_before_snapshot_becomes_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
            created = create_feishu_portfolio_import(
                root,
                settings,
                {
                    "event_id": "evt-1",
                    "market": "us_equities",
                    "ocr_text": "ticker,company,quantity\nMRVL,Marvell,10",
                },
            )
            before = load_dashboard_state(root)
            confirmed = confirm_feishu_portfolio_import(root, settings, created["id"])
            after = load_dashboard_state(root)

        self.assertEqual(created["status"], "pending_confirmation")
        self.assertEqual(before["holdings"][0]["ticker"], "NVDA")
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(after["holdings"][0]["ticker"], "MRVL")

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
        app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        home_renderer = app_js.split("function renderHoldings", 1)[1].split("function renderHoldingConfig", 1)[0]
        config_renderer = app_js.split("function renderHoldingConfig", 1)[1].split("function renderAlerts", 1)[0]

        self.assertNotIn("data-edit-holding", home_renderer)
        self.assertNotIn("data-delete", home_renderer)
        self.assertIn("data-edit-holding", config_renderer)
        self.assertIn("data-delete", config_renderer)

    def test_dashboard_template_labels_displayed_times(self) -> None:
        app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn("function formatTime", app_js)
        self.assertIn("display_timezone", app_js)
        self.assertIn("更新时间 ${formatTime(data.generated_at, data.display_timezone)}", app_js)
        self.assertIn('/static/styles.css', INDEX_HTML)
        self.assertIn('/static/app.js', INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
