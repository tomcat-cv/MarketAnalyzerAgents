from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from dailyresearch.config import deep_merge, load_settings, read_inputs, resolve_path


class ConfigTests(unittest.TestCase):
    def test_deep_merge_preserves_nested_defaults(self) -> None:
        merged = deep_merge(
            {"openai": {"api_base": "a", "tool": "x"}, "backend": "openai"},
            {"openai": {"tool": "y"}},
        )
        self.assertEqual(merged["openai"]["api_base"], "a")
        self.assertEqual(merged["openai"]["tool"], "y")
        self.assertEqual(merged["backend"], "openai")

    def test_resolve_path_keeps_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            absolute = root / "vault"
            self.assertEqual(resolve_path(root, absolute), absolute)
            self.assertEqual(resolve_path(root, "config/a.json"), root / "config/a.json")

    def test_load_settings_accepts_deploy_schedule_env_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "settings.json").write_text("{}", encoding="utf-8")
            overrides = {
                "DAILYRESEARCH_SCHEDULE_MODE": "interval",
                "DAILYRESEARCH_INTERVAL_MINUTES": "360",
                "DAILYRESEARCH_RUN_ON_START": "true",
                "DAILYRESEARCH_PROMPT_PATH": "config/custom-prompt.md",
            }
            with patch.dict(os.environ, overrides, clear=False):
                settings = load_settings(root)

        self.assertEqual(settings["schedule"]["mode"], "interval")
        self.assertEqual(settings["schedule"]["interval_minutes"], 360)
        self.assertTrue(settings["schedule"]["run_on_start"])
        self.assertEqual(settings["prompt"]["extra_instructions_path"], "config/custom-prompt.md")

    def test_read_inputs_loads_prompt_overrides_as_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sources.json").write_text("{}", encoding="utf-8")
            (root / "context.md").write_text("Context guidance", encoding="utf-8")
            (root / "feedback.md").write_text("Feedback guidance", encoding="utf-8")
            (root / "prompt.md").write_text("Prompt guidance", encoding="utf-8")
            inputs = read_inputs(
                root,
                {
                    "context_path": "context.md",
                    "sources_path": "sources.json",
                    "feedback_path": "feedback.md",
                    "prompt": {"extra_instructions_path": "prompt.md"},
                },
            )

        self.assertEqual(inputs["context"], "Context guidance")
        self.assertEqual(inputs["feedback"], "Feedback guidance")
        self.assertEqual(inputs["prompt_extra"], "Prompt guidance")


if __name__ == "__main__":
    unittest.main()
