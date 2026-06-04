from datetime import datetime
import unittest

from dailyresearch.html_renderer import markdown_to_html_body, render_html_document


class HtmlRendererTests(unittest.TestCase):
    def test_renders_links_tables_and_images(self) -> None:
        markdown = """# Daily Research Brief - 2026-05-29

## Signal Radar
| Priority | Topic | Source |
| --- | --- | --- |
| High | AI | [NVIDIA](https://example.com) |

![Chart](https://example.com/chart.png)
"""
        body = markdown_to_html_body(markdown)
        self.assertIn("<table>", body)
        self.assertIn('href="https://example.com"', body)
        self.assertIn('class="brief-image"', body)

    def test_html_document_is_standalone(self) -> None:
        html = render_html_document(
            "# Daily Research Brief - 2026-05-29\n\n## Executive Summary\n- One signal",
            generated_at=datetime(2026, 5, 29, 8, 0),
        )
        self.assertIn("<!doctype html>", html)
        self.assertIn("Daily Research Brief - 2026-05-29", html)
        self.assertIn("2026-05-29 08:00", html)


if __name__ == "__main__":
    unittest.main()
