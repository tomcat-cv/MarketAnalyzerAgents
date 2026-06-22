from datetime import datetime, timedelta, timezone
import unittest

from marketanalyzeragents.collectors import (
    collect_evidence,
    collect_cninfo_announcements,
    collect_rss_items,
    collect_sec_filings,
    collect_yahoo_market_snapshot,
    resolve_research_window,
)


class FakeClient:
    def request_bytes(self, url, **kwargs):
        return b"""<?xml version="1.0"?>
<rss><channel><item>
<title>Official release</title>
<link>https://official.example/release</link>
<pubDate>Thu, 04 Jun 2026 08:00:00 GMT</pubDate>
<description>Verified summary</description>
</item></channel></rss>"""

    def get_json(self, url):
        if url.endswith("company_tickers.json"):
            return {"0": {"ticker": "NVDA", "cik_str": 1045810}}
        return {
            "name": "NVIDIA CORP",
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "filingDate": ["2026-06-04"],
                    "reportDate": ["2026-06-04"],
                    "accessionNumber": ["0001045810-26-000001"],
                    "primaryDocument": ["nvda-8k.htm"],
                    "primaryDocDescription": ["Current report"],
                }
            },
        }

    def post_form_json(self, url, values, headers):
        return {
            "announcements": [
                {
                    "secCode": "688001",
                    "secName": "测试公司",
                    "announcementTitle": "关于<em>半导体</em>项目的公告",
                    "announcementTime": 1780560000000,
                    "adjunctUrl": "finalpage/2026-06-04/test.PDF",
                }
            ]
        }


class NoSummaryFeedClient(FakeClient):
    def request_bytes(self, url, **kwargs):
        return b"""<?xml version="1.0"?>
<rss><channel><item>
<title>Official release title only</title>
<link>https://official.example/title-only</link>
<pubDate>Thu, 04 Jun 2026 08:00:00 GMT</pubDate>
</item></channel></rss>"""


class SecDocumentClient(FakeClient):
    def get_text(self, url):
        return "<html><body><h1>Current report</h1><p>" + ("Verified filing detail. " * 20) + "</p></body></html>"


class YahooClient:
    def get_json(self, url):
        return {
            "chart": {
                "result": [
                    {
                        "meta": {"currency": "USD"},
                        "timestamp": [1780473600, 1780560000],
                        "indicators": {
                            "quote": [
                                {
                                    "close": [100.0, 102.0],
                                    "high": [101.0, 103.0],
                                    "low": [99.0, 100.0],
                                    "volume": [1000, 1200],
                                }
                            ]
                        },
                    }
                ],
                "error": None,
            }
        }


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeClient()
        self.cutoff = datetime(2026, 6, 3, tzinfo=timezone.utc)

    def test_collects_allowed_rss_items(self) -> None:
        items = collect_rss_items(
            client=self.client,
            feed={
                "name": "Official Feed",
                "url": "https://official.example/feed.xml",
                "allowed_domains": ["official.example"],
                "tickers": ["NVDA"],
            },
            cutoff=self.cutoff,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://official.example/release")
        self.assertEqual(items[0].evidence_level, "summary")
        self.assertEqual(items[0].matched_tickers, ["NVDA"])

    def test_rss_without_summary_is_title_only(self) -> None:
        items = collect_rss_items(
            client=NoSummaryFeedClient(),
            feed={
                "name": "Official Feed",
                "url": "https://official.example/feed.xml",
                "allowed_domains": ["official.example"],
            },
            cutoff=self.cutoff,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].evidence_level, "title_only")

    def test_sec_document_text_upgrades_filing_to_summary_evidence(self) -> None:
        items = collect_sec_filings(
            client=SecDocumentClient(),
            sources={
                "portfolios": {
                    "us_equities": {"holdings": [{"ticker": "NVDA", "themes": ["AI"]}]}
                }
            },
            config={
                "forms": ["8-K"],
                "max_per_company": 2,
                "fetch_document_text": True,
                "min_document_chars": 20,
            },
            cutoff=self.cutoff,
        )
        self.assertEqual(items[0].evidence_level, "summary")
        self.assertIn("Verified filing detail", items[0].content)

    def test_collects_cninfo_announcement_titles(self) -> None:
        items = collect_cninfo_announcements(
            client=self.client,
            config={"queries": ["半导体"], "max_per_query": 5},
            cutoff=self.cutoff,
        )
        self.assertEqual(len(items), 1)
        self.assertIn("半导体", items[0].title)
        self.assertTrue(items[0].url.endswith(".PDF"))

    def test_previous_day_window_starts_at_local_midnight(self) -> None:
        china_tz = timezone(timedelta(hours=8))
        start, end, mode = resolve_research_window(
            {"timezone": "Asia/Shanghai", "freshness_window": "previous_day_to_run"},
            now=datetime(2026, 6, 4, 6, 30, tzinfo=china_tz),
        )
        self.assertEqual(mode, "previous_day_to_run")
        self.assertEqual(start, datetime(2026, 6, 2, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 6, 3, 22, 30, tzinfo=timezone.utc))

    def test_rss_excludes_items_after_run_time(self) -> None:
        items = collect_rss_items(
            client=self.client,
            feed={
                "name": "Official Feed",
                "url": "https://official.example/feed.xml",
                "allowed_domains": ["official.example"],
            },
            cutoff=self.cutoff,
            window_end=datetime(2026, 6, 4, 7, 59, tzinfo=timezone.utc),
        )
        self.assertEqual(items, [])

    def test_collects_yahoo_market_snapshot_as_summary(self) -> None:
        items = collect_yahoo_market_snapshot(
            client=YahooClient(),
            instrument={"symbol": "^GSPC", "name": "S&P 500", "topics": ["美股整体市场"]},
            cutoff=datetime(2026, 6, 3, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].evidence_level, "summary")
        self.assertIn("+2.00%", items[0].content)
        self.assertIn("Window range", items[0].content)
        self.assertTrue(items[0].url.startswith("https://query1.finance.yahoo.com/v8/finance/chart/"))
        self.assertEqual(items[0].display_url, "https://finance.yahoo.com/quote/%5EGSPC")

    def test_collect_evidence_adds_configured_holding_price_snapshots(self) -> None:
        pack = collect_evidence(
            settings={
                "timezone": "UTC",
                "freshness_window": "rolling_hours",
                "lookback_hours": 72,
                "collectors": {"max_evidence_items": 20},
            },
            sources={
                "portfolios": {
                    "us_equities": {
                        "holdings": [
                            {
                                "ticker": "NVDA",
                                "company": "NVIDIA",
                                "themes": ["AI infrastructure"],
                            }
                        ]
                    }
                },
                "collectors": {
                    "yahoo_market_snapshots": {"enabled": True, "instruments": []},
                    "sec_filings": {"enabled": False},
                    "rss_feeds": [],
                    "cninfo": {"enabled": False},
                    "finnhub": {"enabled": False},
                },
            },
            client=YahooClient(),
            now=datetime(2026, 6, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(len(pack.items), 1)
        self.assertEqual(pack.items[0].matched_tickers, ["NVDA"])
        self.assertIn("最新股价", pack.items[0].title)
        self.assertIn("Latest available price", pack.items[0].content)
        self.assertIn("holding_price_snapshot", {entry.category for entry in pack.coverage})

if __name__ == "__main__":
    unittest.main()
