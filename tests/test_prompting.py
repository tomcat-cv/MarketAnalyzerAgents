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
        self.assertIn('"portfolio_actions"', user)
        self.assertIn('"NVDA"', user)
        self.assertIn("2026-05-29", user)
        self.assertNotIn("AI agents", user)

    def test_codex_prompt_bans_claude_and_n8n(self) -> None:
        prompt = build_codex_task_prompt(
            settings={"language": "zh-CN", "timezone": "Asia/Shanghai"},
            inputs={"context": "", "feedback": "", "sources": {}},
            run_date=date(2026, 5, 29),
            output_path=Path("briefs/2026-05-29-brief.md"),
        )
        self.assertIn("Do not use Claude", prompt)
        self.assertIn("Do not use n8n", prompt)
        self.assertIn("## 1. 市场总体资讯（可靠信源）", prompt)
        self.assertIn("## 3. 根据市场动态分析持仓应该作何操作", prompt)


if __name__ == "__main__":
    unittest.main()
