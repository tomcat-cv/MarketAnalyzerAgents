from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from marketanalyzeragents.config import deep_merge, load_settings


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
                "MARKET_ANALYZER_AGENTS_PROMPT_PATH": "config/custom-prompt.md",
            }
            with patch.dict(os.environ, overrides, clear=False):
                settings = load_settings(root)

        self.assertEqual(settings["schedule"]["mode"], "interval")
        self.assertEqual(settings["schedule"]["interval_minutes"], 360)
        self.assertTrue(settings["schedule"]["run_on_start"])
        self.assertEqual(settings["prompt"]["extra_instructions_path"], "config/custom-prompt.md")

if __name__ == "__main__":
    unittest.main()
