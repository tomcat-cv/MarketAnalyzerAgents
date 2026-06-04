from pathlib import Path
import tempfile
import unittest

from dailyresearch.config import deep_merge, resolve_path


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


if __name__ == "__main__":
    unittest.main()
