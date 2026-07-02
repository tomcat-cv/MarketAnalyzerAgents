from datetime import date
import unittest

from marketanalyzeragents.evidence import (
    EvidenceItem,
    EvidencePack,
    configured_portfolio_holdings,
    dedupe_evidence,
    evidence_only_brief_markdown,
    filter_evidence_pack,
    model_summary_brief_markdown,
    parse_model_brief,
    source_log_markdown,
    validate_summary_citations,
)


def evidence(url="https://official.example/release", level="summary") -> EvidenceItem:
    return EvidenceItem(
        id="",
        title="Signal",
        published_at="2026-06-04T08:00:00+00:00",
        source_name="Official",
        source_type="primary",
        url=url,
        content="Verified fact",
        evidence_level=level,
    )


class EvidenceContractTests(unittest.TestCase):
    def test_portfolios_remain_separated_and_symbols_are_normalized(self) -> None:
        holdings = configured_portfolio_holdings(
            {
                "portfolios": {
                    "us_equities": {"holdings": [{"ticker": "nvda"}]},
                    "a_share": {"holdings": [{"ticker": "688001"}]},
                }
            }
        )
        self.assertEqual(
            [(item["market"], item["ticker"]) for item in holdings],
            [("a_share", "688001"), ("us_equities", "NVDA")],
        )

    def test_deduplication_assigns_stable_ids(self) -> None:
        items = dedupe_evidence(
            [
                evidence("https://example.com/a"),
                evidence("https://example.com/a#fragment"),
                EvidenceItem(
                    **{
                        **evidence("https://example.com/b").__dict__,
                        "title": "Another signal",
                    }
                ),
            ],
            10,
        )
        self.assertEqual([item.id for item in items], ["EVID-001", "EVID-002"])

    def test_only_summary_evidence_reaches_model_but_other_levels_stay_auditable(self) -> None:
        title_only = evidence(level="title_only")
        pack = EvidencePack("2026-06-04T08:00:00+00:00", 24, items=[title_only])
        self.assertEqual(filter_evidence_pack(pack, {"summary"}).items, [])
        self.assertNotIn(title_only.url, evidence_only_brief_markdown(pack, date(2026, 6, 4)))
        self.assertIn(title_only.url, source_log_markdown(pack))

    def test_market_brief_title_and_market_overview_are_market_specific(self) -> None:
        pack = EvidencePack(
            "2026-06-04T08:00:00+00:00",
            24,
            items=[
                EvidenceItem(
                    **{
                        **evidence("https://finance.yahoo.com/quote/000001.SS").__dict__,
                        "title": "上证指数快照",
                        "matched_topics": ["A股整体市场"],
                    }
                ),
                EvidenceItem(
                    **{
                        **evidence("https://finance.yahoo.com/quote/%5EGSPC").__dict__,
                        "title": "S&P 500 snapshot",
                        "matched_topics": ["美股整体市场"],
                    }
                ),
            ],
        )

        rendered = evidence_only_brief_markdown(pack, date(2026, 6, 4), market="us_equities")

        self.assertIn("# 美股盘前研究简报 - 2026-06-04", rendered)
        self.assertIn("美股市场概览与宏观驱动", rendered)
        self.assertNotIn("A股市场概览", rendered)

    def test_generated_citations_must_come_from_evidence_pack(self) -> None:
        pack = EvidencePack("2026-06-04T08:00:00+00:00", 24, items=[evidence()])
        self.assertEqual(
            validate_summary_citations("[source](https://invented.example/a)", pack),
            ["Unverified citation URL: https://invented.example/a"],
        )

    def test_source_log_prefers_reader_link_and_keeps_data_link_auditable(self) -> None:
        item = evidence("https://query1.finance.yahoo.com/v8/finance/chart/NVDA")
        item.id = "EVID-001"
        item.display_url = "https://finance.yahoo.com/quote/NVDA"
        pack = EvidencePack("2026-06-04T08:00:00+00:00", 24, items=[item])

        rendered = source_log_markdown(pack)

        self.assertIn("[EVID", rendered)
        self.assertIn("](https://finance.yahoo.com/quote/NVDA)", rendered)
        self.assertIn("[数据复核链接](https://query1.finance.yahoo.com/v8/finance/chart/NVDA)", rendered)
        self.assertEqual(validate_summary_citations(rendered, pack), [])

    def test_structured_output_accepts_grounded_hold_decision(self) -> None:
        item = evidence()
        item.id = "EVID-001"
        pack = EvidencePack("2026-06-04T08:00:00+00:00", 24, items=[item])
        result = parse_model_brief(
            """
            {
              "summaries": [{
                "evidence_id": "EVID-001",
                "summary": "Verified fact",
                "analysis": "需要继续核验。"
              }],
              "portfolio_actions": [{
                "ticker": "NVDA",
                "action": "持有",
                "confidence": "低",
                "rationale": "基于给定证据。",
                "evidence_ids": ["EVID-001"],
                "watch_for": "等待更新。"
              }]
            }
            """,
            pack,
            [{"ticker": "NVDA"}],
        )
        self.assertEqual(result.portfolio_actions[0].action, "持有")

    def test_structured_output_accepts_aggregated_market_summaries(self) -> None:
        item = evidence()
        item.id = "EVID-001"
        pack = EvidencePack("2026-06-04T08:00:00+00:00", 24, items=[item])
        result = parse_model_brief(
            """
            {
              "market_summaries": [{
                "topic": "AI infrastructure",
                "summary": "可靠来源显示该主题仍需跟踪。",
                "evidence_ids": ["EVID-001"]
              }],
              "portfolio_actions": [{
                "ticker": "NVDA",
                "action": "持有",
                "confidence": "低",
                "rationale": "基于给定证据。",
                "evidence_ids": ["EVID-001"],
                "watch_for": "等待更新。"
              }]
            }
            """,
            pack,
            [{"ticker": "NVDA"}],
        )

        self.assertEqual(result.market_summaries[0].topic, "AI infrastructure")
        self.assertEqual(result.summaries, {})

    def test_aggregated_brief_does_not_expand_every_evidence_item(self) -> None:
        first = evidence()
        first.id = "EVID-001"
        first.title = "First raw item"
        second = evidence("https://official.example/second")
        second.id = "EVID-002"
        second.title = "Second raw item"
        pack = EvidencePack("2026-06-04T08:00:00+00:00", 24, items=[first, second])
        result = parse_model_brief(
            """
            {
              "market_summaries": [{
                "topic": "Aggregated theme",
                "summary": "两条可靠证据共同指向同一观察主题。",
                "evidence_ids": ["EVID-001", "EVID-002"]
              }],
              "portfolio_actions": [{
                "ticker": "NVDA",
                "action": "持有",
                "confidence": "低",
                "rationale": "基于给定证据。",
                "evidence_ids": ["EVID-001"],
                "watch_for": "等待更新。"
              }]
            }
            """,
            pack,
            [{"ticker": "NVDA"}],
        )

        rendered = model_summary_brief_markdown(
            pack,
            {},
            {},
            date(2026, 6, 4),
            holdings=[{"ticker": "NVDA", "market": "us_equities"}],
            portfolio_actions=result.portfolio_actions,
            market_summaries=result.market_summaries,
        )

        self.assertIn("Aggregated theme", rendered)
        self.assertNotIn("#### EVID-001 · First raw item", rendered)
        self.assertNotIn("#### EVID-002 · Second raw item", rendered)

    def test_directional_decision_without_evidence_is_rejected(self) -> None:
        item = evidence()
        item.id = "EVID-001"
        pack = EvidencePack("2026-06-04T08:00:00+00:00", 24, items=[item])
        with self.assertRaises(ValueError):
            parse_model_brief(
                """
                {
                  "summaries": [{
                    "evidence_id": "EVID-001",
                    "summary": "Verified fact",
                    "analysis": "需要继续核验。"
                  }],
                  "portfolio_actions": [{
                    "ticker": "NVDA",
                    "action": "加仓",
                    "confidence": "低",
                    "rationale": "证据不足。",
                    "evidence_ids": [],
                    "watch_for": "等待更新。"
                  }]
                }
                """,
                pack,
                [{"ticker": "NVDA"}],
            )


if __name__ == "__main__":
    unittest.main()
