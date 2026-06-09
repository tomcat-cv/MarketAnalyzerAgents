import json
import unittest

from marketanalyzeragents.agent_debate import run_agent_debate
from marketanalyzeragents.portfolio_store import Quote


class AgentDebateTests(unittest.TestCase):
    def test_agents_discuss_in_order_and_manager_returns_grounded_decision(self) -> None:
        calls = []

        def invoke(role, _system, user):
            calls.append((role, json.loads(user)))
            if role == "portfolio_manager":
                return json.dumps(
                    {
                        "action": "持有",
                        "confidence": "中",
                        "rationale": "多空证据仍然平衡。",
                        "evidence_ids": ["EVID-001"],
                        "invalidation": "证据或价格结构改变。",
                    },
                    ensure_ascii=False,
                )
            return f"{role} view"

        result = run_agent_debate(
            quote=Quote("us_equities", "NVDA", "2026-06-09T14:00:00+00:00", 102, 100),
            evidence=[{"id": "EVID-001", "content": "Verified news"}],
            invoke=invoke,
        )

        self.assertEqual(
            [role for role, _ in calls],
            [
                "market_analyst",
                "news_analyst",
                "bull_researcher",
                "bear_researcher",
                "risk_manager",
                "portfolio_manager",
            ],
        )
        self.assertIn("Bull:", calls[3][1]["debate"])
        self.assertNotIn("evidence", calls[0][1])
        self.assertIn("evidence", calls[1][1])
        self.assertEqual(result.suggestion["action"], "持有")

    def test_directional_decision_requires_supplied_evidence(self) -> None:
        def invoke(role, _system, _user):
            if role == "portfolio_manager":
                return (
                    '{"action":"加仓","confidence":"高","rationale":"上涨",'
                    '"evidence_ids":[],"invalidation":"下跌"}'
                )
            return "view"

        with self.assertRaises(ValueError):
            run_agent_debate(
                quote=Quote("us_equities", "NVDA", "2026-06-09T14:00:00+00:00", 102),
                evidence=[],
                invoke=invoke,
            )
