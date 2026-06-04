from datetime import date
from pathlib import Path
import tempfile
import unittest

from dailyresearch.writer import output_path_for


class WriterTests(unittest.TestCase):
    def test_output_path_uses_html_extension_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = output_path_for(
                root,
                {"output_dir": "briefs", "output_format": "html"},
                date(2026, 5, 29),
            )
            self.assertEqual(path, root / "briefs" / "2026-05-29-brief.html")

    def test_output_path_can_still_use_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = output_path_for(
                root,
                {"output_dir": "briefs", "output_format": "markdown"},
                date(2026, 5, 29),
            )
            self.assertEqual(path, root / "briefs" / "2026-05-29-brief.md")


if __name__ == "__main__":
    unittest.main()
