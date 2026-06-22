from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from marketanalyzeragents.cli import (
    _brief_notification_blocks,
    _brief_notification_preview,
    _brief_public_url,
    _notify_pre_market_brief,
)


class PreMarketBriefNotificationTests(unittest.TestCase):
    def test_notification_writes_pre_market_brief_to_configured_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "briefs" / "2026-06-09-brief.md"
            output.parent.mkdir()
            output.write_text("# Brief\n\nEvidence summary\n\nPortfolio note", encoding="utf-8")
            settings = {
                "timezone": "Asia/Shanghai",
                "state": {"conversation_outbox": "state/outbox.jsonl"},
                "feishu": {"webhook_url": ""},
            }

            _notify_pre_market_brief(root, settings, date(2026, 6, 9), output)

            outbox = root / "state" / "outbox.jsonl"
            rows = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["type"], "pre_market_brief")
            self.assertEqual(rows[0]["date"], "2026-06-09")
            self.assertIn("Evidence summary", rows[0]["preview"])

    def test_html_notification_preview_extracts_readable_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "brief.html"
            output.write_text(
                """<!doctype html>
<html>
<head><style>body { color: red; }</style></head>
<body>
  <h1>每日研究简报</h1>
  <p>手机端应直接看到这段摘要。</p>
</body>
</html>
""",
                encoding="utf-8",
            )

            preview = _brief_notification_preview(output)

            self.assertIn("每日研究简报", preview)
            self.assertIn("手机端应直接看到这段摘要。", preview)
            self.assertNotIn("color: red", preview)

    def test_html_notification_blocks_preserve_source_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "brief.html"
            output.write_text(
                """<!doctype html>
<html>
<body>
  <article>
    <p>来源链接：<a href="https://finance.yahoo.com/quote/NVDA">Yahoo Finance 原文</a></p>
  </article>
</body>
</html>
""",
                encoding="utf-8",
            )

            blocks = _brief_notification_blocks(output)
            links = [token for block in blocks for token in block if token.get("tag") == "a"]

            self.assertEqual(links[0]["text"], "Yahoo Finance 原文")
            self.assertEqual(links[0]["href"], "https://finance.yahoo.com/quote/NVDA")

    def test_brief_public_url_uses_configured_web_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "briefs" / "2026-06-09-brief.html"
            settings = {"output_dir": "briefs", "web": {"brief_base_url": "https://example.com/reports/"}}

            url = _brief_public_url(root, settings, output)

            self.assertEqual(url, "https://example.com/reports/2026-06-09-brief.html")


if __name__ == "__main__":
    unittest.main()
