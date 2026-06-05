import unittest

from datetime import date

from dailyresearch.evidence import (
    EvidenceItem,
    EvidencePack,
    PortfolioAction,
    SourceCoverage,
    configured_a_share_holdings,
    configured_holdings,
    dedupe_evidence,
    evidence_only_brief_markdown,
    filter_evidence_pack,
    model_summary_brief_markdown,
    parse_model_brief,
    parse_model_summaries,
    source_log_markdown,
    validate_summary_citations,
)


def item(url: str, title: str = "Signal") -> EvidenceItem:
    return EvidenceItem(
        id="",
        title=title,
        published_at="2026-06-04T08:00:00+00:00",
        source_name="Official",
        source_type="primary",
        url=url,
        content="Verified fact",
    )


class EvidenceTests(unittest.TestCase):
    def test_reads_separate_us_and_a_share_portfolio_config(self) -> None:
        sources = {
            "portfolios": {
                "us_equities": {"holdings": [{"ticker": "nvda", "company": "NVIDIA"}]},
                "a_share": {"holdings": [{"ticker": "688001", "company": "测试公司"}]},
            }
        }
        self.assertEqual(configured_holdings(sources)[0]["ticker"], "NVDA")
        self.assertEqual(configured_a_share_holdings(sources)[0]["ticker"], "688001")

    def test_dedupe_assigns_stable_evidence_ids(self) -> None:
        items = dedupe_evidence(
            [
                item("https://example.com/a"),
                item("https://example.com/a#fragment"),
                item("https://example.com/b", "Another signal"),
            ],
            max_items=10,
        )
        self.assertEqual([value.id for value in items], ["EVID-001", "EVID-002"])

    def test_rejects_unverified_citation_urls(self) -> None:
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[item("https://official.example/release")],
        )
        errors = validate_summary_citations(
            "[Source](https://invented.example/article)",
            pack,
        )
        self.assertEqual(errors, ["Unverified citation URL: https://invented.example/article"])

    def test_accepts_exact_evidence_url(self) -> None:
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[item("https://official.example/release")],
        )
        self.assertEqual(
            validate_summary_citations("[Source](https://official.example/release)", pack),
            [],
        )

    def test_accepts_url_used_as_markdown_label_and_target(self) -> None:
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[item("https://official.example/release")],
        )
        self.assertEqual(
            validate_summary_citations(
                "[https://official.example/release](https://official.example/release)",
                pack,
            ),
            [],
        )

    def test_title_only_evidence_is_not_sent_for_model_summary(self) -> None:
        title_only = item("https://official.example/release")
        title_only.evidence_level = "title_only"
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[title_only],
        )
        self.assertEqual(filter_evidence_pack(pack, {"summary"}).items, [])
        brief = evidence_only_brief_markdown(pack, date(2026, 6, 4))
        self.assertIn("不直接用于持仓操作判断", brief)
        self.assertIn("https://official.example/release", brief)

    def test_model_evidence_pack_excludes_collection_operational_metadata(self) -> None:
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[item("https://official.example/release")],
            errors=["RSS failed with HTTP 403"],
            coverage=[
                SourceCoverage(
                    name="Example RSS",
                    category="official_rss",
                    status="failed",
                    detail="HTTP 403",
                )
            ],
        )
        model_pack = filter_evidence_pack(pack, {"summary"})
        self.assertEqual(model_pack.errors, [])
        self.assertEqual(model_pack.coverage, [])

    def test_parses_structured_model_summaries(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        summaries = parse_model_summaries(
            '```json\n{"summaries":[{"evidence_id":"EVID-001","summary":"经验证的摘要。"}]}\n```',
            pack,
        )
        self.assertEqual(summaries, {"EVID-001": "经验证的摘要。"})

    def test_rejects_unknown_model_summary_id(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        with self.assertRaises(ValueError):
            parse_model_summaries(
                '{"summaries":[{"evidence_id":"EVID-999","summary":"摘要。"}]}',
                pack,
            )

    def test_rejects_numbers_not_present_in_evidence(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        with self.assertRaises(ValueError):
            parse_model_summaries(
                '{"summaries":[{"evidence_id":"EVID-001","summary":"增长了20%。"}]}',
                pack,
            )

    def test_accepts_equivalent_number_formatting(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        evidence.content = "Close: 4,088.8840."
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        summaries = parse_model_summaries(
            '{"summaries":[{"evidence_id":"EVID-001","summary":"收盘4088.884。"}]}',
            pack,
        )
        self.assertEqual(summaries["EVID-001"], "收盘4088.884。")

    def test_accepts_two_decimal_rounding_from_market_data(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        evidence.content = "Latest available price: 7584.3101. Window high: 7598.1899."
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        summaries = parse_model_summaries(
            '{"summaries":[{"evidence_id":"EVID-001","summary":"最新价7584.31，最高7598.19。"}]}',
            pack,
        )
        self.assertIn("7584.31", summaries["EVID-001"])

    def test_accepts_chinese_ten_thousand_unit_conversion(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        evidence.content = "Sold shares: 1000000. Gift shares: 307500. Post-transaction holdings: 6092271."
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        summaries = parse_model_summaries(
            '{"summaries":[{"evidence_id":"EVID-001","summary":"卖出100万股，赠与30.75万股，交易后约609万股。"}]}',
            pack,
        )
        self.assertIn("100万股", summaries["EVID-001"])

    def test_accepts_whole_percent_rounding(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        evidence.content = "Window range percent versus window low: 1.95%."
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        summaries = parse_model_summaries(
            '{"summaries":[{"evidence_id":"EVID-001","summary":"振幅约2%。"}]}',
            pack,
        )
        self.assertIn("2%", summaries["EVID-001"])

    def test_document_form_names_are_not_treated_as_numeric_claims(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        summaries = parse_model_summaries(
            '{"summaries":[{"evidence_id":"EVID-001","summary":"关注后续6-K或20-F文件。"}]}',
            pack,
        )
        self.assertIn("6-K", summaries["EVID-001"])

    def test_accepts_verified_published_date_in_summary(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        summaries = parse_model_summaries(
            '{"summaries":[{"evidence_id":"EVID-001","summary":"该条目发布于2026年6月4日。"}]}',
            pack,
        )
        self.assertIn("2026年6月4日", summaries["EVID-001"])

    def test_parses_grounded_portfolio_actions(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        result = parse_model_brief(
            """
            {
              "summaries": [{
                "evidence_id": "EVID-001",
                "summary": "经验证的摘要。",
                "analysis": "这条证据说明需要继续核验。"
              }],
              "portfolio_actions": [{
                "ticker": "NVDA",
                "action": "观察",
                "confidence": "中",
                "rationale": "该判断基于经验证的市场信息。",
                "evidence_ids": ["EVID-001"],
                "watch_for": "等待公司可靠来源确认。"
              }]
            }
            """,
            pack,
            [{"ticker": "NVDA", "company": "NVIDIA"}],
        )
        self.assertEqual(result.summaries["EVID-001"], "经验证的摘要。")
        self.assertEqual(result.analyses["EVID-001"], "这条证据说明需要继续核验。")
        self.assertEqual(result.portfolio_actions[0].action, "观察")

    def test_normalizes_unsupported_no_event_claim(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        result = parse_model_brief(
            """
            {
              "summaries": [{
                "evidence_id": "EVID-001",
                "summary": "经验证的摘要。",
                "analysis": "这条证据说明需要继续核验。"
              }],
              "portfolio_actions": [{
                "ticker": "NVDA",
                "action": "观察",
                "confidence": "低",
                "rationale": "窗口内无NVDA直接相关事件，暂不调整。",
                "evidence_ids": [],
                "watch_for": "等待可靠来源确认。"
              }]
            }
            """,
            pack,
            [{"ticker": "NVDA", "company": "NVIDIA"}],
        )
        self.assertIn("配置的可靠信源未采集到", result.portfolio_actions[0].rationale)
        self.assertNotIn("窗口内无NVDA", result.portfolio_actions[0].rationale)

    def test_analysis_can_compare_against_other_supplied_evidence_numbers(self) -> None:
        holding_evidence = item("https://official.example/holding")
        holding_evidence.id = "EVID-001"
        market_evidence = item("https://official.example/market")
        market_evidence.id = "EVID-002"
        market_evidence.content = "PHLX Semiconductor Index down -2.15%."
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[holding_evidence, market_evidence],
        )
        result = parse_model_brief(
            """
            {
              "summaries": [
                {
                  "evidence_id": "EVID-001",
                  "summary": "经验证的摘要。",
                  "analysis": "可与EVID-002的-2.15%作横向比较。"
                },
                {
                  "evidence_id": "EVID-002",
                  "summary": "半导体指数下跌2.15%。",
                  "analysis": "这是板块背景证据。"
                }
              ],
              "portfolio_actions": [{
                "ticker": "NVDA",
                "action": "观察",
                "confidence": "中",
                "rationale": "该判断基于经验证的市场信息。",
                "evidence_ids": ["EVID-001"],
                "watch_for": "等待公司可靠来源确认。"
              }]
            }
            """,
            pack,
            [{"ticker": "NVDA", "company": "NVIDIA"}],
        )
        self.assertIn("-2.15%", result.analyses["EVID-001"])

    def test_rejects_ungrounded_add_action(self) -> None:
        evidence = item("https://official.example/release")
        evidence.id = "EVID-001"
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[evidence],
        )
        with self.assertRaises(ValueError):
            parse_model_brief(
                """
                {
                  "summaries": [{
                    "evidence_id": "EVID-001",
                    "summary": "经验证的摘要。",
                    "analysis": "这条证据说明需要继续核验。"
                  }],
                  "portfolio_actions": [{
                    "ticker": "NVDA",
                    "action": "加仓",
                    "confidence": "低",
                    "rationale": "证据不足。",
                    "evidence_ids": [],
                    "watch_for": "等待可靠来源确认。"
                  }]
                }
                """,
                pack,
                [{"ticker": "NVDA", "company": "NVIDIA"}],
            )

    def test_builds_deterministic_brief_around_model_summary(self) -> None:
        summary_item = item("https://official.example/release")
        summary_item.id = "EVID-001"
        summary_item.matched_tickers = ["NVDA"]
        title_item = item("https://official.example/title", "Title only")
        title_item.id = "EVID-002"
        title_item.evidence_level = "title_only"
        title_item.source_name = "巨潮资讯网"
        title_item.matched_topics = ["A股", "A股重点方向：半导体"]
        price_item = item("https://query1.finance.yahoo.com/v8/finance/chart/NVDA", "NVIDIA：最新股价 102.0000 USD，窗口上涨 2.00%")
        price_item.id = "EVID-003"
        price_item.source_name = "Yahoo Finance"
        price_item.source_type = "market_data_aggregator"
        price_item.matched_tickers = ["NVDA"]
        pack = EvidencePack(
            retrieved_at="2026-06-04T08:00:00+00:00",
            lookback_hours=24,
            items=[summary_item, title_item, price_item],
            timezone="Asia/Shanghai",
            coverage=[
                SourceCoverage(
                    name="SEC EDGAR",
                    category="official_filing",
                    status="no_items",
                    detail="Queries every configured US holding.",
                )
            ],
        )
        brief = model_summary_brief_markdown(
            pack,
            {"EVID-001": "经验证的摘要。", "EVID-003": "NVDA最新股价102.0000美元，窗口上涨2.00%。"},
            {"EVID-001": "这条证据需要关注后续影响。", "EVID-003": "价格快照只说明短期表现，不能单独解释原因。"},
            date(2026, 6, 4),
            holdings=[{"ticker": "NVDA", "company": "NVIDIA"}],
            portfolio_actions=[
                PortfolioAction(
                    ticker="NVDA",
                    action="持有",
                    confidence="中",
                    rationale="可靠来源证据支持维持当前计划。",
                    evidence_ids=["EVID-001"],
                    watch_for="等待下一份可靠公司更新。",
                )
            ],
        )
        self.assertIn("经验证的摘要", brief)
        self.assertIn("## 1. 市场总体资讯（可靠信源）", brief)
        self.assertIn("## 2. 持仓公司相关资讯（可靠信源）", brief)
        self.assertIn("## 3. 根据市场动态分析持仓应该作何操作", brief)
        self.assertIn("| NVDA | 持有 | 中 |", brief)
        self.assertIn("2026-06-04 16:00", brief)
        self.assertIn("最新股价 102.0000 USD", brief)
        self.assertIn("来源链接", brief)
        self.assertIn("价格快照只说明短期表现", brief)
        self.assertIn("Title only", brief)
        self.assertIn("A股公告标题复核队列", brief)
        self.assertNotIn("## 附录", brief)
        self.assertNotIn("采集器覆盖", brief)
        self.assertNotIn("market_data_aggregator", brief)

        source_log = source_log_markdown(pack)
        self.assertIn("## 采集覆盖", source_log)
        self.assertIn("SEC EDGAR", source_log)
        self.assertIn("已查询，本窗口无条目", source_log)


if __name__ == "__main__":
    unittest.main()
