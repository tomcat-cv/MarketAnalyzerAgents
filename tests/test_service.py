import argparse
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import call, patch
from zoneinfo import ZoneInfo

from marketanalyzeragents.analysis_system import ContentPack, _open_market_suggestion_interval, generate_intraday_suggestion, service_loop
from marketanalyzeragents.cli import build_parser, command_report, command_suggest


class ServiceCommandTests(unittest.TestCase):
    def _write_project(self, root: Path) -> None:
        (root / "config").mkdir()
        (root / "config" / "settings.json").write_text(
            json.dumps(
                {
                    "backend": "dry-run",
                    "timezone": "Asia/Shanghai",
                    "sources_path": "config/sources.json",
                    "state": {"analysis_dir": "state/analysis"},
                    "market_config_paths": {},
                    "market_overview": {"enabled": False},
                }
            ),
            encoding="utf-8",
        )
        (root / "config" / "sources.json").write_text(
            json.dumps(
                {
                    "portfolios": {"us_equities": {"holdings": [{"ticker": "NVDA", "symbol": "NVDA"}]}},
                    "focus_topics": [],
                    "official_sources": [],
                    "social_sources": {},
                }
            ),
            encoding="utf-8",
        )

    def test_cli_exposes_only_core_product_commands(self) -> None:
        parser = build_parser()

        self.assertEqual(set(parser._subparsers._group_actions[0].choices), {"web", "report", "suggest", "service"})

    def test_service_command_exposes_loop_options(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["service", "--tick-seconds", "5", "--run-on-start"])

        self.assertEqual(args.command, "service")
        self.assertEqual(args.tick_seconds, 5)
        self.assertTrue(args.run_on_start)

    def test_web_command_loads_dotenv_before_starting_server(self) -> None:
        args = argparse.Namespace(host="0.0.0.0", port=8765)
        root = Path("/tmp/project")
        with patch("marketanalyzeragents.cli.find_project_root", return_value=root), patch(
            "marketanalyzeragents.cli.load_dotenv"
        ) as load_env, patch("marketanalyzeragents.cli.run_web_server") as run_web:
            exit_code = build_parser().parse_args(["web"]).func(args)

        self.assertEqual(exit_code, 0)
        load_env.assert_called_once_with(root / ".env")
        run_web.assert_has_calls([call(host="0.0.0.0", port=8765, root=root)])

    def test_report_command_generates_archive_with_dry_run_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            args = argparse.Namespace(slot="08:00", backend="dry-run")
            with patch("marketanalyzeragents.cli.find_project_root", return_value=root), patch(
                "marketanalyzeragents.analysis_system._collect_official",
                return_value=([], []),
            ), patch(
                "marketanalyzeragents.analysis_system.current_market_sentiment",
                return_value=({"value": "61", "label": "Greed", "status": "ok", "components": []}, []),
            ), contextlib.redirect_stdout(io.StringIO()):
                exit_code = command_report(args)

            reports = list((root / "state" / "analysis" / "reports").glob("*.json"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(reports), 1)

    def test_suggest_command_uses_core_suggestion_generator(self) -> None:
        args = argparse.Namespace(backend="dry-run")
        with patch("marketanalyzeragents.cli.find_project_root", return_value=Path("/tmp/project")), patch(
            "marketanalyzeragents.cli.generate_intraday_suggestion",
            return_value={
                "id": "suggestion-1",
                "title": "盘中操作建议",
                "generated_at": "2026-07-08T10:00:00+08:00",
                "quote_count": 0,
            },
        ) as generate, contextlib.redirect_stdout(io.StringIO()):
            exit_code = command_suggest(args)

        self.assertEqual(exit_code, 0)
        generate.assert_called_once_with(Path("/tmp/project"), backend="dry-run")

    def test_open_market_suggestion_interval_uses_active_market_settings(self) -> None:
        interval = _open_market_suggestion_interval(
            {
                "intraday_suggestion_interval_seconds": 1800,
                "markets": {
                    "a_share": {"poll_interval_seconds": 900},
                    "us_equities": {"poll_interval_seconds": 1200},
                },
            },
            {"a_share": {"state": "closed"}, "us_equities": {"state": "open"}},
        )

        self.assertEqual(interval, 1200)

    def test_intraday_suggestion_records_quote_value_errors_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)

            with patch("marketanalyzeragents.analysis_system.fetch_market_data", side_effect=ValueError("No usable market data returned for NVDA")), patch(
                "marketanalyzeragents.analysis_system.collect_content",
                return_value=ContentPack(official=[], social_posts=[], source_warnings=[]),
            ), patch(
                "marketanalyzeragents.analysis_system.current_market_sentiment",
                return_value=({"value": "61", "label": "Greed", "status": "ok", "components": []}, []),
            ):
                result = generate_intraday_suggestion(root, backend="dry-run")

        self.assertEqual(result["quote_count"], 0)
        self.assertIn("No usable market data returned for NVDA", "\n".join(result["warnings"]))

    def test_service_loop_records_task_error_and_keeps_loop_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
            settings["report_schedule"] = ["08:00"]
            (root / "config" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
            fixed_now = datetime(2026, 7, 10, 8, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

            with patch("marketanalyzeragents.analysis_system.beijing_now", return_value=fixed_now), patch(
                "marketanalyzeragents.analysis_system.generate_market_report",
                side_effect=ValueError("report boom"),
            ), patch("marketanalyzeragents.analysis_system.dashboard_state", return_value={"markets": {}}), patch(
                "marketanalyzeragents.analysis_system.time.sleep",
                side_effect=SystemExit,
            ), self.assertRaises(SystemExit):
                service_loop(root, tick_seconds=1)

            status = json.loads((root / "state" / "service_status.json").read_text(encoding="utf-8"))

        self.assertEqual(status["last_error"]["task"], "scheduled_report")
        self.assertEqual(status["last_error"]["message"], "report boom")


if __name__ == "__main__":
    unittest.main()
