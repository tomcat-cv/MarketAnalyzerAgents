import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from marketanalyzeragents.analysis_system import _open_market_suggestion_interval
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
                    "fear_greed": {},
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

    def test_report_command_generates_archive_with_dry_run_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_project(root)
            args = argparse.Namespace(slot="08:00", backend="dry-run")
            with patch("marketanalyzeragents.cli.find_project_root", return_value=root), patch(
                "marketanalyzeragents.analysis_system._collect_official",
                return_value=([], []),
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


if __name__ == "__main__":
    unittest.main()
