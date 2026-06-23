from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from marketanalyzeragents.config import deep_merge, load_market_settings, load_settings


class ConfigTests(unittest.TestCase):
    def test_deep_merge_preserves_nested_defaults(self) -> None:
        merged = deep_merge(
            {"openai": {"api_base": "a", "tool": "x"}, "backend": "openai"},
            {"openai": {"tool": "y"}},
        )
        self.assertEqual(merged["openai"]["api_base"], "a")
        self.assertEqual(merged["openai"]["tool"], "y")
        self.assertEqual(merged["backend"], "openai")

    def test_load_settings_accepts_deploy_schedule_env_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "settings.json").write_text("{}", encoding="utf-8")
            overrides = {
                "MARKET_ANALYZER_AGENTS_SCHEDULE_MODE": "interval",
                "MARKET_ANALYZER_AGENTS_INTERVAL_MINUTES": "360",
                "MARKET_ANALYZER_AGENTS_RUN_ON_START": "true",
                "MARKET_ANALYZER_AGENTS_HEALTH_PATH": "state/custom-health.json",
                "MARKET_ANALYZER_AGENTS_RETENTION_DAYS": "30",
            }
            with patch.dict(os.environ, overrides, clear=False):
                settings = load_settings(root)

        self.assertEqual(settings["schedule"]["mode"], "interval")
        self.assertEqual(settings["schedule"]["interval_minutes"], 360)
        self.assertTrue(settings["schedule"]["run_on_start"])
        self.assertEqual(settings["service"]["health_path"], "state/custom-health.json")
        self.assertEqual(settings["service"]["retention_days"], 30)

    def test_market_settings_load_independent_schedule_and_calendar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config" / "markets").mkdir(parents=True)
            (root / "config" / "settings.json").write_text(
                '{"market_config_paths": {"a_share": "config/markets/a_share.json"}}',
                encoding="utf-8",
            )
            (root / "config" / "markets" / "a_share.json").write_text(
                """
                {
                  "schedule": {"mode": "daily", "time": "09:00"},
                  "market": {"holidays": ["2026-10-01"], "poll_interval_seconds": 30},
                  "output_dir": "briefs/a_share"
                }
                """,
                encoding="utf-8",
            )

            settings = load_market_settings(root, load_settings(root), "a_share")

        self.assertEqual(settings["schedule"]["time"], "09:00")
        self.assertEqual(settings["markets"]["a_share"]["holidays"], ["2026-10-01"])
        self.assertEqual(settings["markets"]["a_share"]["poll_interval_seconds"], 30)
        self.assertEqual(settings["output_dir"], "briefs/a_share")

if __name__ == "__main__":
    unittest.main()
