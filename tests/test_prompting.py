from datetime import date
from pathlib import Path
import unittest

from dailyresearch.prompting import build_codex_task_prompt, build_openai_messages


class PromptingTests(unittest.TestCase):
    def test_openai_prompt_contains_required_sections(self) -> None:
        system, user = build_openai_messages(
            settings={"language": "zh-CN", "timezone": "Asia/Shanghai"},
            inputs={
                "context": "AI agents",
                "feedback": "Prefer links",
                "prompt_extra": "Use a compact decision table",
                "sources": {
                    "portfolio": {
                        "holdings": [{"ticker": "NVDA", "company": "NVIDIA", "themes": ["AI"]}]
                    }
                },
            },
            run_date=date(2026, 5, 29),
            output_path=Path("briefs/2026-05-29-brief.md"),
        )
        self.assertIn("evidence-grounded market research analyst", system)
        self.assertIn('"summaries"', user)
        self.assertIn('"analysis"', user)
        self.assertIn('"portfolio_actions"', user)
        self.assertIn('"NVDA"', user)
        self.assertIn("2026-05-29", user)
        self.assertIn("price-only movement", user)
        self.assertIn("Do not calculate new averages", user)
        self.assertIn("AI agents", system)
        self.assertIn("Prefer links", system)
        self.assertIn("Use a compact decision table", system)
        self.assertIn("not factual evidence", system)

    def test_codex_prompt_bans_claude_and_n8n(self) -> None:
        prompt = build_codex_task_prompt(
            settings={"language": "zh-CN", "timezone": "Asia/Shanghai"},
            inputs={"context": "Focus on semiconductors", "feedback": "", "prompt_extra": "", "sources": {}},
            run_date=date(2026, 5, 29),
            output_path=Path("briefs/2026-05-29-brief.md"),
        )
        self.assertIn("Do not use Claude", prompt)
        self.assertIn("Do not use n8n", prompt)
        self.assertIn("price-only movement", prompt)
        self.assertIn("Do not calculate new averages", prompt)
        self.assertIn("Do not include an appendix", prompt)
        self.assertIn("## 1. 市场概览", prompt)
        self.assertIn("## 2. 重点主题雷达", prompt)
        self.assertIn("## 4. 根据市场动态分析持仓应该作何操作", prompt)
        self.assertIn("Focus on semiconductors", prompt)
        self.assertIn("not factual evidence", prompt)


if __name__ == "__main__":
    unittest.main()
